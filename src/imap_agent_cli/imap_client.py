from __future__ import annotations

from contextlib import AbstractContextManager
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .errors import AppError
from .mime import attachment_infos, message_headers, parse_message, render_body, save_attachments
from .models import Defaults, Profile
from .search import build_criteria


SPECIAL_USE_MAP = {
    "\\Inbox": "inbox",
    "\\Sent": "sent",
    "\\Drafts": "drafts",
    "\\Trash": "trash",
    "\\Junk": "junk",
    "\\Archive": "archive",
}


class ImapSession(AbstractContextManager["ImapSession"]):
    def __init__(self, profile: Profile, defaults: Defaults) -> None:
        self.profile = profile
        self.defaults = defaults
        self.server: Any = None

    def __enter__(self) -> "ImapSession":
        try:
            from imapclient import IMAPClient
        except ImportError as exc:
            raise AppError(
                "config_invalid",
                "IMAPClient is not installed. Run with 'uv run' or install imap-agent-cli with dependencies.",
            ) from exc
        if not self.profile.password:
            raise AppError("auth_failed", f"missing password for profile '{self.profile.name}'.")
        try:
            self.server = IMAPClient(
                self.profile.host,
                port=self.profile.port,
                ssl=self.profile.tls,
                timeout=self.profile.connect_timeout_seconds,
            )
            self.server.login(self.profile.username, self.profile.password)
        except Exception as exc:
            raise AppError("connection_failed", f"failed to connect or authenticate: {exc}") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.server is None:
            return
        try:
            self.server.logout()
        except Exception:
            pass

    def _select(self, folder: str, *, readonly: bool = True) -> None:
        # SELECT does not mutate message state; no-seen behavior is enforced by
        # BODY.PEEK[] fetches. Some servers, including pymap on Windows, hang on
        # the IMAPClient EXAMINE/read-only path.
        try:
            self.server.select_folder(folder, readonly=False)
        except Exception as exc:
            raise AppError("folder_not_found", f"folder '{folder}' could not be selected: {exc}") from exc

    def folders(self) -> dict[str, Any]:
        folders = []
        for flags, delimiter, name in self.server.list_folders():
            flag_values = [flag.decode() if isinstance(flag, bytes) else str(flag) for flag in flags]
            special = None
            for flag in flag_values:
                special = SPECIAL_USE_MAP.get(flag, special)
            selectable = "\\Noselect" not in flag_values
            total = None
            unread = None
            if selectable:
                try:
                    status = self.server.folder_status(name, ["MESSAGES", "UNSEEN"])
                    total = _lookup_status(status, "MESSAGES")
                    unread = _lookup_status(status, "UNSEEN")
                except Exception:
                    pass
            folders.append(
                {
                    "name": name,
                    "delimiter": delimiter,
                    "selectable": selectable,
                    "special_use": special,
                    "flags": flag_values,
                    "total": total,
                    "unread": unread,
                    "children": [],
                }
            )
        return {"profile": self.profile.name, "folders": folders}

    def _folder_names_for_scope(self, folder: str, scope: str) -> list[str]:
        if scope == "folder":
            return [folder]
        all_folders = self.folders()["folders"]
        selectable = [item for item in all_folders if item["selectable"]]
        if scope == "recursive":
            return [item["name"] for item in selectable if item["name"] == folder or item["name"].startswith(folder)]
        if scope == "all":
            excluded = set(self.defaults.exclude_special_folders_from_all)
            return [item["name"] for item in selectable if item.get("special_use") not in excluded]
        raise AppError("invalid_request", f"unsupported folder scope '{scope}'.")

    def search(
        self,
        *,
        folder: str,
        scope: str,
        subject: str | None,
        sender: str | None,
        since: str | None,
        before: str | None,
        max_results: int,
    ) -> dict[str, Any]:
        criteria = build_criteria(subject=subject, sender=sender, since=since, before=before)
        results: list[dict[str, Any]] = []
        for current_folder in self._folder_names_for_scope(folder, scope):
            self._select(current_folder, readonly=True)
            try:
                uids = list(self.server.search(criteria))
            except Exception as exc:
                raise AppError("server_error", f"search failed in folder '{current_folder}': {exc}") from exc
            for uid in reversed(uids):
                if len(results) >= max_results:
                    break
                results.append(self._summary(current_folder, int(uid)))
            if len(results) >= max_results:
                break
        return {
            "profile": self.profile.name,
            "query": {
                "folder": folder,
                "scope": scope,
                "subject": subject,
                "from": sender,
                "since": since,
                "before": before,
                "max_results": max_results,
            },
            "results": results,
            "truncated": len(results) >= max_results,
        }

    def _fetch_raw(self, folder: str, uid: int) -> bytes:
        self._select(folder, readonly=True)
        try:
            data = self.server.fetch([uid], ["BODY.PEEK[]"])
        except Exception as exc:
            raise AppError("message_not_found", f"message UID {uid} could not be fetched from '{folder}'.") from exc
        entry = data.get(uid) or data.get(str(uid)) or data.get(bytes(str(uid), "ascii"))
        if not entry:
            raise AppError("message_not_found", f"message UID {uid} was not found in '{folder}'.")
        for key, value in entry.items():
            key_text = key.decode() if isinstance(key, bytes) else str(key)
            if "BODY" in key_text.upper():
                return value
        raise AppError("server_error", f"server did not return message body for UID {uid}.")

    def _summary(self, folder: str, uid: int) -> dict[str, Any]:
        raw = self._fetch_raw(folder, uid)
        message = parse_message(raw)
        headers = message_headers(message)
        attachments = attachment_infos(message)
        return {
            "folder": folder,
            "uid": uid,
            "message_id": str(message.get("message-id", "")),
            "date": str(message.get("date", "")),
            "from": headers["from"],
            "to": headers["to"],
            "cc": headers["cc"],
            "subject": headers["subject"],
            "has_attachments": bool(attachments),
            "attachment_count": len(attachments),
            "size_bytes": len(raw),
        }

    def read(self, *, folder: str, uid: int, body_format: str, max_body_chars: int) -> dict[str, Any]:
        raw = self._fetch_raw(folder, uid)
        message = parse_message(raw)
        return message_payload(
            profile=self.profile.name,
            folder=folder,
            uid=uid,
            message=message,
            body_format=body_format,
            max_body_chars=max_body_chars,
        )

    def attachments(self, *, folder: str, uid: int) -> dict[str, Any]:
        raw = self._fetch_raw(folder, uid)
        message = parse_message(raw)
        return {
            "profile": self.profile.name,
            "folder": folder,
            "uid": uid,
            "attachments": [info for info in attachment_infos(message)],
        }

    def download_attachments(
        self,
        *,
        folder: str,
        uid: int,
        output_dir: Path,
        part_id: str | None,
        all_parts: bool,
        include_inline: bool,
        overwrite: bool,
    ) -> dict[str, Any]:
        raw = self._fetch_raw(folder, uid)
        message = parse_message(raw)
        return {
            "profile": self.profile.name,
            "folder": folder,
            "uid": uid,
            "saved": save_attachments(
                message,
                output_dir,
                part_id=part_id,
                all_parts=all_parts,
                include_inline=include_inline,
                overwrite=overwrite,
            ),
        }

    def resolve_drafts_folder(self, override: str | None = None) -> str:
        if override:
            return override
        if self.profile.drafts_folder:
            return self.profile.drafts_folder
        folders = self.folders()["folders"]
        for item in folders:
            if item.get("special_use") == "drafts":
                return str(item["name"])
        names = {str(item["name"]).lower(): str(item["name"]) for item in folders}
        for candidate in ("drafts", "inbox.drafts", "[gmail]/drafts"):
            if candidate in names:
                return names[candidate]
        raise AppError("drafts_folder_not_found", "could not auto-detect Drafts folder; configure drafts_folder.")

    def append_draft(self, message: EmailMessage, *, drafts_folder: str | None = None) -> dict[str, Any]:
        folder = self.resolve_drafts_folder(drafts_folder)
        raw = message.as_bytes(policy=message.policy)
        try:
            result = self.server.append(folder, raw)
        except Exception as exc:
            raise AppError("server_error", f"failed to append draft to '{folder}': {exc}") from exc
        return {
            "profile": self.profile.name,
            "drafts_folder": folder,
            "created": True,
            "append_uid": _extract_append_uid(result),
            "message_id": str(message.get("message-id", "")),
            "subject": str(message.get("subject", "")),
            "to": message.get("to", ""),
            "attachment_count": len(list(message.iter_attachments())),
        }


def _lookup_status(status: dict[Any, Any], name: str) -> int | None:
    for key, value in status.items():
        key_text = key.decode() if isinstance(key, bytes) else str(key)
        if key_text.upper() == name:
            return int(value)
    return None


def _extract_append_uid(result: Any) -> int | None:
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, int):
                return item
    return None


def message_payload(
    *,
    profile: str,
    folder: str,
    uid: int,
    message: EmailMessage,
    body_format: str,
    max_body_chars: int,
) -> dict[str, Any]:
    return {
        "profile": profile,
        "folder": folder,
        "uid": uid,
        "message_id": str(message.get("message-id", "")),
        "date": str(message.get("date", "")),
        "headers": message_headers(message),
        "body": render_body(message, body_format, max_body_chars),
        "attachments": [info for info in attachment_infos(message)],
    }
