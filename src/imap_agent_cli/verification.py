"""Read-only, bounded onboarding verification. Never fetch a message body."""
from __future__ import annotations

import time

from .errors import AppError
from .imap_client import ImapSession, METADATA_FETCH_FIELDS
from .models import Defaults, Profile


def profile_metadata(profile: Profile) -> dict:
    return {"name": profile.name, "host": profile.host, "port": profile.port,
            "username": profile.username, "password_env": profile.password_env,
            "has_password": bool(profile.password), "credential_source": profile.credential_source,
            "tls": profile.tls, "ssl_mode": profile.ssl_mode, "auth": profile.auth}


def verify_profile(profile: Profile, defaults: Defaults, *, local: bool = False,
                   session_factory=ImapSession) -> dict:
    checks = [{"name": "profile_resolved", "ok": True},
              {"name": "password_available", "ok": bool(profile.password), "password_env": profile.password_env}]
    report = {"ok": False, "profile": profile_metadata(profile), "checks": checks,
              "verification": "not_checked", "draft_creation": "not_tested"}
    if not profile.password:
        checks[-1]["error"] = "No credential is available. Run uvx imap-agent-cli setup in your terminal for this profile."
        return report
    if local:
        report["ok"] = True
        return report
    try:
        with session_factory(profile, defaults) as session:
            deadline = time.monotonic() + 60
            def bounded(operation):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AppError("connection_timeout", "Verification exceeded 60 seconds. Check the server and network, then retry config check.")
                session.server.socket().settimeout(min(profile.read_timeout_seconds, remaining))
                return operation()

            checks.append({"name": "login", "ok": True, "security": session.security})
            checks.append({"name": "capabilities", "ok": True, "values": bounded(session.capabilities)})
            # One LIST, no per-folder STATUS requests. Bound retained results.
            folders = bounded(session.server.list_folders)
            if len(folders) > 1000:
                raise AppError("verification_limit", "The server returned more than 1000 folders. Verification stopped without changing the mailbox.")
            checks.append({"name": "folders", "ok": True, "count": len(folders)})
            selectable = {}
            special_drafts = []
            for flags, _, name in folders:
                normalized = {flag.decode().lower() if isinstance(flag, bytes) else flag.lower() for flag in flags}
                if "\\noselect" not in normalized:
                    selectable[str(name)] = normalized
                    if "\\drafts" in normalized:
                        special_drafts.append(str(name))
            selected = bounded(lambda: session._select(defaults.default_folder, readonly=True))
            checks.append({"name": "default_folder", "ok": True, "folder": defaults.default_folder, "mode": "read_only"})
            # UIDNEXT bounds the number of possible returned UIDs, even if the
            # server has millions of messages. No open-ended '*' search.
            uidnext = int(selected.get(b"UIDNEXT", selected.get("UIDNEXT", 0)))
            if uidnext > 1:
                start = max(1, uidnext - min(defaults.max_scan, 250))
                uids = bounded(lambda: session.server.search(["UID", f"{start}:{uidnext - 1}"]))
                if len(uids) > 250 or any(not start <= int(uid) < uidnext for uid in uids):
                    raise AppError("server_error", "The server returned UIDs outside the verification limit.")
                if uids:
                    data = bounded(lambda: session.server.fetch([max(uids)], METADATA_FETCH_FIELDS))
                    if not data:
                        raise AppError("server_error", "Message metadata was unavailable. A message may have changed during verification. Retry config check.")
                    checks.append({"name": "message_metadata", "ok": True, "status": "verified"})
                else:
                    checks.append({"name": "message_metadata", "ok": True, "status": "not_tested", "reason": "no_messages_in_bounded_range"})
            else:
                checks.append({"name": "message_metadata", "ok": True, "status": "not_tested", "reason": "empty_mailbox_or_uidnext_unavailable"})
            draft = profile.drafts_folder
            if not draft and len(special_drafts) == 1:
                draft = special_drafts[0]
            if not draft and not special_drafts:
                candidates = [name for name in selectable if name.lower() in {"drafts", "inbox.drafts", "[gmail]/drafts"}]
                if len(candidates) == 1:
                    draft = candidates[0]
            draft_ok = draft in selectable
            item = {"name": "drafts_folder", "ok": draft_ok, "required": False, "folder": draft or None}
            if not draft_ok:
                item["error"] = "Reading is ready. Drafts was not resolved. Run setup with --drafts-folder and an existing folder before creating drafts."
            checks.append(item)
            report["verification"] = "verified"
    except AppError as exc:
        checks.append({"name": exc.code, "ok": False, "error": exc.message})
        report["verification"] = "failed"
    except Exception:
        checks.append({"name": "server_error", "ok": False, "error": "IMAP verification failed. Check server access and retry uvx imap-agent-cli config check."})
        report["verification"] = "failed"
    report["ok"] = all(item["ok"] for item in checks if item.get("required", True))
    return report
