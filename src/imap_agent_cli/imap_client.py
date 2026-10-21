from __future__ import annotations

import re
import imaplib
import socket
import ssl
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import AppError
from .mime import attachment_infos, header_value, message_headers, parse_message, render_body, save_attachments
from .models import Defaults, Profile
from .mime_structure import body_part, message_structure, structure_attachments
from .render import write_diagnostic
from .search import build_criteria


SPECIAL_USE_MAP = {
    "\\Inbox": "inbox",
    "\\Sent": "sent",
    "\\Drafts": "drafts",
    "\\Trash": "trash",
    "\\Junk": "junk",
    "\\Archive": "archive",
}

METADATA_FETCH_FIELDS = ["ENVELOPE", "INTERNALDATE", "RFC822.SIZE", "BODYSTRUCTURE", "FLAGS"]
THREAD_HEADER_FETCH = "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES DATE FROM TO CC SUBJECT)]"
READ_METADATA_FIELDS = ["BODY.PEEK[HEADER]", "BODYSTRUCTURE"]
READ_ATTEMPTS = 2


def _network_error(exc: Exception, operation: str, phase: str, *, attempts: int = 1, write: bool = False) -> AppError:
    timeout = False
    cause: BaseException | None = exc
    visited: set[int] = set()
    while cause is not None and id(cause) not in visited:
        visited.add(id(cause))
        timeout |= isinstance(cause, (TimeoutError, socket.timeout)) or (
            isinstance(cause, (OSError, imaplib.IMAP4.abort)) and "timed out" in str(cause).lower()
        )
        cause = cause.__cause__ or cause.__context__
    network = timeout or isinstance(exc, (OSError, imaplib.IMAP4.abort, EOFError))
    code = "connection_timeout" if timeout else "connection_failed" if network else "server_error"
    message = f"IMAP {phase} timed out." if timeout else f"IMAP {phase} failed."
    if write and network:
        message += " The draft may have been created. Verify Drafts before trying again."
    return AppError(code, message, retryable=network and not write, operation=operation, phase=phase,
                    attempts=attempts, outcome="unknown" if write and network else None)


class ImapSession(AbstractContextManager["ImapSession"]):
    def __init__(self, profile: Profile, defaults: Defaults) -> None:
        self.profile = profile
        self.defaults = defaults
        self.server: Any = None
        self.security: dict[str, Any] = {}
        self.timings = False
        self.read_attempts = READ_ATTEMPTS

    def __enter__(self) -> "ImapSession":
        from .providers import reject_microsoft
        from .config import validate_profile
        validate_profile(self.profile)
        reject_microsoft(self.profile.host)
        try:
            from imapclient import IMAPClient
            from imapclient.imapclient import SocketTimeout
        except ImportError:
            raise AppError("config_invalid", "IMAPClient is not installed. Run uvx imap-agent-cli or install this package with its dependencies.") from None
        if not self.profile.password:
            raise AppError("auth_failed", "No credential is available. Run uvx imap-agent-cli setup in your terminal for this profile.")
        ssl_mode = self.profile.ssl_mode
        encrypted = False
        method = "plain"
        try:
            implicit = self.profile.tls and ssl_mode != "disabled"
            try:
                self.server = IMAPClient(
                    self.profile.host, port=self.profile.port, ssl=implicit,
                    ssl_context=ssl.create_default_context() if implicit else None,
                    timeout=SocketTimeout(connect=self.profile.connect_timeout_seconds, read=self.profile.read_timeout_seconds),
                )
            except ssl.SSLCertVerificationError:
                raise
            except (OSError, imaplib.IMAP4.error):
                if ssl_mode != "preferred" or not implicit:
                    raise
                self.server = IMAPClient(
                    self.profile.host, port=self.profile.port, ssl=False,
                    timeout=SocketTimeout(connect=self.profile.connect_timeout_seconds, read=self.profile.read_timeout_seconds),
                )
                implicit = False
            if ssl_mode != "disabled":
                if not implicit:
                    self.server.starttls(ssl.create_default_context())
                encrypted = True
                method = "implicit_tls" if implicit else "starttls"
        except Exception as exc:
            self._disconnect_quiet()
            if isinstance(exc, ssl.SSLCertVerificationError):
                raise AppError("tls_failed", "The server certificate could not be verified. Check the hostname and trusted certificates, then run uvx imap-agent-cli config check.", operation="connect", phase="connect", attempts=1) from None
            if isinstance(exc, (TimeoutError, socket.timeout)):
                raise AppError("connection_timeout", "The IMAP connection timed out. Check network access from this execution environment, then run uvx imap-agent-cli config check.", retryable=True, operation="connect", phase="connect", attempts=1) from None
            raise AppError("connection_failed", "Could not establish the configured IMAP connection. Check the host, port, TLS settings, and network access. Run uvx imap-agent-cli setup to correct this profile.", retryable=True, operation="connect", phase="connect", attempts=1) from None
        # Do not retry authentication via another transport or credential source.
        try:
            self.server.login(self.profile.username, self.profile.password)
        except (TimeoutError, socket.timeout) as exc:
            self._disconnect_quiet()
            raise _network_error(exc, "connect", "login") from None
        except imaplib.IMAP4.abort as exc:
            self._disconnect_quiet()
            raise _network_error(exc, "connect", "login") from None
        except imaplib.IMAP4.error:
            self._logout_quiet()
            if self.profile.credential_source.startswith("environment:"):
                recovery = "Update " + self.profile.credential_source.partition(":")[2] + " in this execution environment, then rerun uvx imap-agent-cli config check."
            else:
                selector = " --profile " + self.profile.name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.profile.name) else ""
                recovery = f"Run uvx imap-agent-cli setup{selector} --replace-password in your terminal."
            raise AppError("auth_failed", "The server rejected the credential. Check the login name and provider app-password requirements. " + recovery) from None
        except Exception as exc:
            self._disconnect_quiet()
            raise _network_error(exc, "connect", "login") from None
        self.security = {"ssl_mode": ssl_mode, "encrypted": encrypted, "method": method}
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._logout_quiet()

    def _logout_quiet(self) -> None:
        if self.server is None:
            return
        try:
            self.server.logout()
        except Exception:
            pass
        finally:
            self.server = None

    def _select(self, folder: str, *, readonly: bool = True) -> dict:
        def select():
            try:
                return self.server.select_folder(folder, readonly=readonly)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                raise AppError("folder_not_found", "The requested folder could not be opened read-only. Check its name and access. Run uvx imap-agent-cli setup --default-folder INBOX to use the inbox. No read-write fallback was attempted.", operation="select", phase="select") from None
        return self._read_call("select", select)

    def _disconnect_quiet(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass

    def _read_call(self, operation: str, action: Callable[[], Any], *, folder: str | None = None,
                   phase: str | None = None) -> Any:
        """Retry a safe read once on a fresh authenticated connection."""
        phase = phase or operation
        started = time.monotonic()
        attempts = 0
        try:
            for attempts in range(1, self.read_attempts + 1):
                failed_phase = phase
                try:
                    if attempts > 1:
                        try:
                            self.__enter__()
                        except AppError as exc:
                            exc.details.update(operation=operation, phase="reconnect", attempts=attempts)
                            raise
                        if folder is not None:
                            failed_phase = "select"
                            self.server.select_folder(folder, readonly=True)
                        failed_phase = phase
                    return action()
                except AppError:
                    raise
                except Exception as exc:
                    error = _network_error(exc, operation, failed_phase, attempts=attempts)
                    if error.retryable:
                        self._disconnect_quiet()
                        if attempts < self.read_attempts:
                            continue
                    raise error from None
        finally:
            if self.timings:
                try:
                    write_diagnostic({"diagnostic": {"operation": operation, "phase": phase,
                                      "elapsed_ms": round((time.monotonic() - started) * 1000, 3), "attempts": attempts}})
                except AppError:
                    pass

    def capabilities(self) -> list[str]:
        values = self._read_call("capabilities", lambda: self.server.capabilities())
        return sorted(_text(value).upper() for value in values)

    def folders(self) -> dict[str, Any]:
        folders = []
        for flags, delimiter, name in self._read_call("list_folders", lambda: self.server.list_folders()):
            flag_values = [flag.decode() if isinstance(flag, bytes) else str(flag) for flag in flags]
            special = None
            for flag in flag_values:
                special = SPECIAL_USE_MAP.get(flag, special)
            selectable = "\\Noselect" not in flag_values
            total = None
            unread = None
            if selectable:
                try:
                    status = self._read_call("folder_status", lambda: self.server.folder_status(name, ["MESSAGES", "UNSEEN"]))
                    total = _lookup_status(status, "MESSAGES")
                    unread = _lookup_status(status, "UNSEEN")
                except AppError as exc:
                    if exc.retryable or exc.code != "server_error":
                        raise
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
            return [
                item["name"]
                for item in selectable
                if _folder_is_self_or_child(str(item["name"]), folder, item.get("delimiter"))
            ]
        if scope == "all":
            excluded = set(self.defaults.exclude_special_folders_from_all)
            return [item["name"] for item in selectable if not _exclude_from_all(item, excluded)]
        raise AppError("invalid_request", f"unsupported folder scope '{scope}'.")

    def search(
        self,
        *,
        folder: str,
        scope: str,
        subject: str | None,
        sender: str | None,
        recipient: str | None,
        message_id: str | None,
        text: str | None,
        since: str | None,
        before: str | None,
        unseen: bool,
        seen: bool,
        answered: bool,
        flagged: bool,
        larger: int | None,
        smaller: int | None,
        has_attachments: bool,
        max_results: int,
        max_scan: int,
        sort: str,
        order: str,
    ) -> dict[str, Any]:
        criteria = build_criteria(
            subject=subject,
            sender=sender,
            recipient=recipient,
            message_id=message_id,
            text=text,
            since=since,
            before=before,
            unseen=unseen,
            seen=seen,
            answered=answered,
            flagged=flagged,
            larger=larger,
            smaller=smaller,
        )
        if max_results <= 0:
            raise AppError("invalid_request", "--max-results must be greater than zero.")
        if max_scan <= 0:
            raise AppError("invalid_request", "--max-scan must be greater than zero.")
        if sort not in {"uid", "date"} or order not in {"asc", "desc"}:
            raise AppError("invalid_request", "--sort must be uid or date and --order must be asc or desc.")
        results: list[dict[str, Any]] = []
        scanned_count = 0
        matched_count = 0
        truncated = False
        folder_names = self._folder_names_for_scope(folder, scope)
        for folder_index, current_folder in enumerate(folder_names):
            self._select(current_folder, readonly=True)
            uids = self._read_call("search", lambda: list(self.server.search(criteria)), folder=current_folder)
            ordered_uids = sorted((int(uid) for uid in uids), reverse=(order == "desc"))
            if len(ordered_uids) > max_scan:
                truncated = True
            scan_uids = ordered_uids[:max_scan]
            offset = 0
            while offset < len(scan_uids):
                remaining = max_results - len(results)
                size = 50 if sort == "date" else min(50, remaining)
                chunk = scan_uids[offset:offset + size]
                offset += len(chunk)
                scanned_count += len(chunk)
                summaries = self._summaries(current_folder, chunk)
                if has_attachments:
                    summaries = [item for item in summaries if item["has_attachments"]]
                matched_count += len(summaries)
                results.extend(summaries)
                if sort == "uid" and len(results) >= max_results:
                    truncated |= offset < len(ordered_uids) or folder_index + 1 < len(folder_names)
                    break
            if sort == "uid" and len(results) >= max_results:
                break
        if sort == "date":
            results.sort(key=_summary_date_sort_key, reverse=(order == "desc"))
            truncated |= len(results) > max_results
        results = results[:max_results]
        return {
            "profile": self.profile.name,
            "query": {
                "folder": folder,
                "scope": scope,
                "subject": subject,
                "from": sender,
                "to": recipient,
                "message_id": message_id,
                "text": text,
                "since": since,
                "before": before,
                "unseen": unseen,
                "seen": seen,
                "answered": answered,
                "flagged": flagged,
                "larger": larger,
                "smaller": smaller,
                "has_attachments": has_attachments,
                "max_results": max_results,
                "max_scan": max_scan,
                "sort": sort,
                "order": order,
            },
            "results": results,
            "scanned_count": scanned_count,
            "matched_count": matched_count,
            "truncated": truncated,
        }

    def _summaries(self, folder: str, uids: Iterable[int]) -> list[dict[str, Any]]:
        uid_list = list(uids)
        if not uid_list:
            return []
        summaries: list[dict[str, Any]] = []
        for chunk in _chunks(uid_list, 50):
            data = self._read_call("search", lambda: self.server.fetch(chunk, METADATA_FETCH_FIELDS), folder=folder, phase="metadata_fetch")
            for uid in chunk:
                entry = _fetch_entry(data, uid)
                if entry is None:
                    continue
                summaries.append(_summary_from_fetch(folder, uid, entry))
        return summaries

    def _fetch_raw(self, folder: str, uid: int) -> bytes:
        self._select(folder, readonly=True)
        data = self._read_call("read", lambda: self.server.fetch([uid], ["BODY.PEEK[]"]), folder=folder, phase="body_fetch")
        entry = _fetch_entry(data, uid)
        if entry is None:
            raise AppError("message_not_found", f"message UID {uid} was not found in '{folder}'.")
        raw = _fetch_value(entry, ["BODY[]"])
        if isinstance(raw, bytes):
            return raw
        raise AppError("server_error", f"server did not return message body for UID {uid}.")

    def read(
        self,
        *,
        folder: str,
        uid: int,
        body_format: str,
        max_body_chars: int,
        include_attachments: str,
    ) -> dict[str, Any]:
        if body_format not in {"metadata", "plain", "html", "raw-html", "markdown"} or max_body_chars < 0:
            raise AppError("invalid_request", "Use a supported body format and a nonnegative --max-body-chars.")
        message, leaves = self._message_metadata(folder, uid)
        if body_format != "metadata":
            selected = body_part(leaves, body_format)
            if selected:
                section, node = selected
                data = self._read_call("read", lambda: self.server.fetch([uid], [f"BODY.PEEK[{section}]"]),
                                       folder=folder, phase="body_fetch")
                entry = _fetch_entry(data, uid)
                if entry is None:
                    raise AppError("message_not_found", "The message disappeared before its body could be read.", operation="read", phase="body_fetch")
                raw = _fetch_value(entry, [f"BODY[{section}]"])
                if not isinstance(raw, bytes):
                    raise AppError("server_error", "The server did not return the requested MIME part.", operation="read", phase="body_fetch")
                node.set_payload(raw)
        payload = message_payload(
            profile=self.profile.name,
            folder=folder,
            uid=uid,
            message=message,
            body_format=body_format,
            max_body_chars=max_body_chars,
            include_attachments=include_attachments,
        )
        attachments = structure_attachments(leaves)
        payload.update(has_attachments=bool(attachments), attachment_count=len(attachments))
        if "attachments" in payload:
            payload["attachments"] = attachments
        return payload

    def _message_metadata(self, folder: str, uid: int) -> tuple[EmailMessage, list]:
        self._select(folder, readonly=True)
        data = self._read_call("read", lambda: self.server.fetch([uid], READ_METADATA_FIELDS),
                               folder=folder, phase="metadata_fetch")
        entry = _fetch_entry(data, uid)
        if entry is None:
            raise AppError("message_not_found", "The requested message was not found.", operation="read", phase="metadata_fetch")
        headers = _fetch_value(entry, ["BODY[HEADER]"])
        structure = _fetch_value(entry, ["BODYSTRUCTURE"])
        if not isinstance(headers, bytes) or structure is None:
            raise AppError("server_error", "The server did not return message headers and MIME structure.", operation="read", phase="metadata_fetch")
        return message_structure(headers, structure)

    def attachments(self, *, folder: str, uid: int) -> dict[str, Any]:
        _, leaves = self._message_metadata(folder, uid)
        return {
            "profile": self.profile.name,
            "folder": folder,
            "uid": uid,
            "attachments": structure_attachments(leaves),
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
        raise AppError("drafts_folder_not_found", "Could not auto-detect Drafts folder. Configure drafts_folder.")

    def append_draft(self, message: EmailMessage, *, drafts_folder: str | None = None) -> dict[str, Any]:
        folder = self.resolve_drafts_folder(drafts_folder)
        raw = message.as_bytes(policy=message.policy)
        try:
            result = self.server.append(folder, raw)
        except Exception as exc:
            error = _network_error(exc, "append", "append", write=True)
            if error.code in {"connection_failed", "connection_timeout"}:
                self._disconnect_quiet()
            raise error from None
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

    def thread(
        self,
        *,
        folder: str,
        uid: int,
        max_messages: int,
        max_scan: int,
        include_body: str,
        body_format: str,
        max_body_chars: int,
    ) -> dict[str, Any]:
        if max_messages <= 0:
            raise AppError("invalid_request", "--max-messages must be greater than zero.")
        if max_scan <= 0:
            raise AppError("invalid_request", "--max-scan must be greater than zero.")
        if include_body not in {"none", "latest", "all"}:
            raise AppError("invalid_request", "--include-body must be none, latest, or all.")
        source_message, _ = self._message_metadata(folder, uid)
        source_ids = _thread_ids_from_message(source_message)
        if not source_ids:
            source_ids = {f"uid:{uid}"}
        self._select(folder, readonly=True)
        all_uids = self._read_call("thread", lambda: sorted((int(item) for item in self.server.search(["ALL"])), reverse=True), folder=folder, phase="search")
        scan_uids = all_uids[:max_scan]
        headers = self._thread_headers(folder, scan_uids)
        related = [
            item
            for item in headers
            if item["uid"] == uid or _thread_header_matches(item, source_ids)
        ]
        if not any(item["uid"] == uid for item in related):
            related.append(_thread_header_from_message(folder, uid, source_message))
        related.sort(key=_thread_sort_key)
        truncated = len(related) > max_messages or len(all_uids) > max_scan
        messages = related[-max_messages:]
        body_uids: set[int] = set()
        if include_body == "all":
            body_uids = {int(item["uid"]) for item in messages}
        elif include_body == "latest" and messages:
            body_uids = {int(messages[-1]["uid"])}
        for item in messages:
            if int(item["uid"]) in body_uids:
                payload = self.read(folder=folder, uid=int(item["uid"]), body_format=body_format,
                                    max_body_chars=max_body_chars, include_attachments="metadata")
                item["body"] = payload["body"]
                item["attachments"] = payload["attachments"]
        return {
            "profile": self.profile.name,
            "folder": folder,
            "source_uid": uid,
            "thread_ids": sorted(source_ids),
            "messages": messages,
            "scanned_count": len(scan_uids),
            "matched_count": len(related),
            "truncated": truncated,
        }

    def _thread_headers(self, folder: str, uids: Iterable[int]) -> list[dict[str, Any]]:
        uid_list = list(uids)
        if not uid_list:
            return []
        headers: list[dict[str, Any]] = []
        for chunk in _chunks(uid_list, 50):
            data = self._read_call("thread", lambda: self.server.fetch(chunk, [THREAD_HEADER_FETCH, "INTERNALDATE", "FLAGS"]), folder=folder, phase="metadata_fetch")
            for uid in chunk:
                entry = _fetch_entry(data, uid)
                if entry is None:
                    continue
                raw = _fetch_value(entry, [THREAD_HEADER_FETCH, "BODY"])
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", errors="replace")
                if not isinstance(raw, bytes):
                    continue
                message = parse_message(raw + b"\r\n\r\n")
                item = _thread_header_from_message(folder, uid, message)
                internal_date = _fetch_value(entry, ["INTERNALDATE"])
                if internal_date is not None:
                    item["internal_date"] = _datetime_text(internal_date)
                item["flags"] = _flags(_fetch_value(entry, ["FLAGS"]))
                headers.append(item)
        return headers


def _lookup_status(status: dict[Any, Any], name: str) -> int | None:
    for key, value in status.items():
        key_text = key.decode() if isinstance(key, bytes) else str(key)
        if key_text.upper() == name:
            return int(value)
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _header_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        return str(make_header(decode_header(text)))
    except Exception:
        return text


def _datetime_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _fetch_entry(data: dict[Any, Any], uid: int) -> dict[Any, Any] | None:
    for key in (uid, str(uid), bytes(str(uid), "ascii")):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return None


def _fetch_value(entry: dict[Any, Any], names: Iterable[str]) -> Any:
    wanted = {name.upper().replace("BODY.PEEK[", "BODY[") for name in names}
    for key, value in entry.items():
        key_text = _text(key).upper()
        normalized = key_text.replace("BODY.PEEK[", "BODY[")
        if normalized in wanted or "BODY" in wanted and normalized.startswith("BODY["):
            return value
    return None


def _flags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return sorted(_text(item) for item in value)
    return [_text(value)]


def _folder_is_self_or_child(name: str, root: str, delimiter: Any) -> bool:
    if name == root:
        return True
    delimiter_text = _text(delimiter)
    if not delimiter_text:
        return False
    return name.startswith(f"{root}{delimiter_text}")


def _exclude_from_all(folder: dict[str, Any], excluded: set[str]) -> bool:
    special = str(folder.get("special_use") or "").lower()
    if special in excluded:
        return True
    name = str(folder.get("name") or "").lower()
    delimiter = _text(folder.get("delimiter"))
    leaf = name.rsplit(delimiter, 1)[-1] if delimiter else name
    return leaf in excluded or leaf in {"junk", "spam", "bulk mail"}


def _address_dicts(values: Any) -> list[dict[str, str]]:
    if not values:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    addresses: list[dict[str, str]] = []
    for value in values:
        name = _header_text(getattr(value, "name", ""))
        mailbox = _text(getattr(value, "mailbox", ""))
        host = _text(getattr(value, "host", ""))
        if not mailbox and isinstance(value, (list, tuple)) and len(value) >= 4:
            name = _header_text(value[0])
            mailbox = _text(value[2])
            host = _text(value[3])
        email = f"{mailbox}@{host}" if mailbox and host else mailbox
        if email:
            addresses.append({"name": name, "email": email})
    return addresses


def _bodystructure_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bytes):
        return [_text(value).lower()]
    if isinstance(value, str):
        return [value.lower()]
    if isinstance(value, (list, tuple)):
        tokens: list[str] = []
        for item in value:
            tokens.extend(_bodystructure_tokens(item))
        return tokens
    if isinstance(value, dict):
        tokens = []
        for key, item in value.items():
            tokens.extend(_bodystructure_tokens(key))
            tokens.extend(_bodystructure_tokens(item))
        return tokens
    return [_text(value).lower()]


def _attachment_count_from_bodystructure(value: Any) -> int:
    try:
        _, leaves = message_structure(b"", value)
        return len(structure_attachments(leaves))
    except AppError:
        # Preserve summary compatibility with incomplete server structures.
        pass
    tokens = _bodystructure_tokens(value)
    return sum(1 for token in tokens if token == "attachment")


def _summary_from_fetch(folder: str, uid: int, entry: dict[Any, Any]) -> dict[str, Any]:
    envelope = _fetch_value(entry, ["ENVELOPE"])
    bodystructure = _fetch_value(entry, ["BODYSTRUCTURE"])
    attachment_count = _attachment_count_from_bodystructure(bodystructure)
    internal_date = _fetch_value(entry, ["INTERNALDATE"])
    size = _fetch_value(entry, ["RFC822.SIZE"])
    subject = _header_text(getattr(envelope, "subject", "")) if envelope is not None else ""
    date_value = _datetime_text(getattr(envelope, "date", "")) if envelope is not None else ""
    message_id = _text(getattr(envelope, "message_id", "")) if envelope is not None else ""
    return {
        "folder": folder,
        "uid": uid,
        "message_id": message_id,
        "date": date_value,
        "internal_date": _datetime_text(internal_date),
        "from": _address_dicts(getattr(envelope, "from_", None) or getattr(envelope, "from", None)),
        "to": _address_dicts(getattr(envelope, "to", None)),
        "cc": _address_dicts(getattr(envelope, "cc", None)),
        "subject": subject,
        "has_attachments": attachment_count > 0,
        "attachment_count": attachment_count,
        "size_bytes": int(size) if size is not None else None,
        "flags": _flags(_fetch_value(entry, ["FLAGS"])),
    }


def _summary_date_sort_key(item: dict[str, Any]) -> tuple[float, int]:
    value = str(item.get("internal_date") or item.get("date") or "")
    try:
        try:
            date = datetime.fromisoformat(value)
        except ValueError:
            date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        timestamp = date.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        timestamp = float("-inf")
    return timestamp, int(item.get("uid") or 0)


def _message_ids_from_header(value: str) -> set[str]:
    return set(match.group(0) for match in re.finditer(r"<[^<>]+>", value or ""))


def _thread_ids_from_message(message: EmailMessage) -> set[str]:
    ids: set[str] = set()
    for header in ("message-id", "in-reply-to", "references"):
        ids.update(_message_ids_from_header(header_value(message, header)))
    return ids


def _thread_header_from_message(folder: str, uid: int, message: EmailMessage) -> dict[str, Any]:
    headers = message_headers(message)
    return {
        "folder": folder,
        "uid": uid,
        "message_id": header_value(message, "message-id"),
        "in_reply_to": header_value(message, "in-reply-to"),
        "references": header_value(message, "references"),
        "date": header_value(message, "date"),
        "internal_date": "",
        "from": headers["from"],
        "to": headers["to"],
        "cc": headers["cc"],
        "subject": headers["subject"],
        "flags": [],
    }


def _thread_header_matches(item: dict[str, Any], source_ids: set[str]) -> bool:
    item_ids = set()
    item_ids.update(_message_ids_from_header(str(item.get("message_id") or "")))
    item_ids.update(_message_ids_from_header(str(item.get("in_reply_to") or "")))
    item_ids.update(_message_ids_from_header(str(item.get("references") or "")))
    return bool(item_ids & source_ids)


def _thread_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    return (str(item.get("internal_date") or item.get("date") or ""), int(item.get("uid") or 0))


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
    include_attachments: str = "metadata",
) -> dict[str, Any]:
    attachments = [info for info in attachment_infos(message)]
    if include_attachments in {"false", "none"}:
        include_attachments = "none"
    elif include_attachments in {"true", "metadata"}:
        include_attachments = "metadata"
    else:
        raise AppError("invalid_request", "--include-attachments must be none or metadata.")
    payload: dict[str, Any] = {
        "profile": profile,
        "folder": folder,
        "uid": uid,
        "message_id": header_value(message, "message-id"),
        "date": header_value(message, "date"),
        "headers": message_headers(message),
        "body": render_body(message, body_format, max_body_chars),
        "has_attachments": bool(attachments),
        "attachment_count": len(attachments),
    }
    if include_attachments == "metadata":
        payload["attachments"] = attachments
    return payload
