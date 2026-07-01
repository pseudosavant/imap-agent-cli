from __future__ import annotations

import hashlib
import html
import mimetypes
import os
import re
import shutil
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, make_msgid
from pathlib import Path
from typing import Any, Iterable

from .errors import AppError
from .models import AttachmentInfo


BODY_FORMATS = {"html", "markdown", "plain", "raw-html", "metadata"}


def parse_message(raw: bytes) -> EmailMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    if not isinstance(parsed, EmailMessage):
        raise AppError("server_error", "Parsed message was not an EmailMessage.")
    return parsed


def header_value(message: Message, name: str, default: str = "") -> str:
    target = name.lower()
    try:
        for key, value in message.raw_items():
            if key.lower() == target:
                return str(value)
    except Exception:
        pass
    try:
        value = message.get(name, default)
    except Exception:
        return default
    return str(value) if value is not None else default


def addresses(header_values: Iterable[str | None]) -> list[dict[str, str]]:
    raw = [value for value in header_values if value]
    return [{"name": name, "email": email} for name, email in getaddresses(raw)]


def message_headers(message: Message) -> dict[str, Any]:
    return {
        "subject": header_value(message, "subject"),
        "from": addresses(message.get_all("from", [])),
        "to": addresses(message.get_all("to", [])),
        "cc": addresses(message.get_all("cc", [])),
        "bcc": addresses(message.get_all("bcc", [])),
        "reply_to": addresses(message.get_all("reply-to", [])),
    }


def _part_payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _body_parts(message: EmailMessage) -> tuple[str | None, str | None]:
    plain: str | None = None
    html_body: str | None = None
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            ctype = part.get_content_type().lower()
            if ctype == "text/plain" and plain is None:
                plain = _part_payload_text(part)
            elif ctype == "text/html" and html_body is None:
                html_body = _part_payload_text(part)
    else:
        ctype = message.get_content_type().lower()
        if ctype == "text/html":
            html_body = _part_payload_text(message)
        elif ctype == "text/plain":
            plain = _part_payload_text(message)
    return plain, html_body


def sanitize_html(value: str) -> str:
    try:
        import bleach
    except ImportError:
        return html.escape(value)
    allowed_tags = {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
    allowed_attrs = {
        "a": ["href", "title"],
        "abbr": ["title"],
        "td": ["colspan", "rowspan"],
        "th": ["colspan", "rowspan"],
    }
    cleaned = bleach.clean(
        value,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    return bleach.linkify(cleaned)


def html_to_plain(value: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return re.sub(r"<[^>]+>", " ", value)
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "iframe", "object"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def html_to_markdown(value: str) -> str:
    try:
        from markdownify import markdownify
    except ImportError:
        return html_to_plain(value)
    return markdownify(sanitize_html(value), heading_style="ATX")


def render_body(message: EmailMessage, body_format: str, max_chars: int) -> dict[str, Any]:
    if body_format not in BODY_FORMATS:
        raise AppError("invalid_request", f"unsupported body format '{body_format}'.")
    if body_format == "metadata":
        return {"format": "metadata", "content": "", "sanitized": False, "truncated": False, "max_chars": max_chars}
    plain, html_body = _body_parts(message)
    sanitized = False
    if body_format == "html":
        if html_body is not None:
            content = sanitize_html(html_body)
            sanitized = True
        else:
            content = plain or ""
    elif body_format == "raw-html":
        content = html_body if html_body is not None else (plain or "")
    elif body_format == "markdown":
        content = html_to_markdown(html_body) if html_body is not None else (plain or "")
        sanitized = html_body is not None
    else:
        content = plain if plain is not None else html_to_plain(html_body or "")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {
        "format": body_format,
        "content": content,
        "sanitized": sanitized,
        "truncated": truncated,
        "max_chars": max_chars,
    }


def attachment_infos(message: EmailMessage) -> list[AttachmentInfo]:
    infos: list[AttachmentInfo] = []
    index = 1
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        content_id = part.get("content-id")
        is_attachment = disposition == "attachment" or bool(filename)
        is_inline = disposition == "inline"
        if not is_attachment and not is_inline:
            continue
        payload = part.get_payload(decode=True) or b""
        infos.append(
            AttachmentInfo(
                part_id=str(index),
                filename=filename or f"part-{index}",
                content_type=part.get_content_type(),
                size_bytes=len(payload),
                content_id=content_id.strip("<>") if content_id else None,
                inline=is_inline,
            )
        )
        index += 1
    return infos


def safe_filename(filename: str) -> str:
    base = os.path.basename(filename).strip().replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)
    base = base.strip(" .")
    return base or "attachment"


def save_attachments(
    message: EmailMessage,
    output_dir: Path,
    *,
    part_id: str | None = None,
    all_parts: bool = False,
    include_inline: bool = False,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected: list[tuple[str, Message, Path, bytes, bool]] = []
    index = 1
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        is_inline = disposition == "inline"
        is_attachment = disposition == "attachment" or bool(filename)
        if not is_attachment and not (include_inline and is_inline):
            continue
        current_id = str(index)
        index += 1
        if not all_parts and current_id != part_id:
            continue
        if is_inline and not include_inline:
            continue
        payload = part.get_payload(decode=True) or b""
        target = output_dir / safe_filename(filename or f"part-{current_id}")
        selected.append((current_id, part, target, payload, is_inline))
    if not selected:
        raise AppError("attachment_not_found", "no matching attachment was found.")
    targets = [target.resolve() for _, _, target, _, _ in selected]
    if len(set(targets)) != len(targets):
        raise AppError("invalid_request", "multiple attachments resolve to the same output filename.")
    for target in targets:
        if target.exists() and not overwrite:
            raise AppError("invalid_request", f"refusing to overwrite existing file: {target}")
    saved: list[dict[str, Any]] = []
    for current_id, part, target, payload, is_inline in selected:
        target.write_bytes(payload)
        saved.append(
            {
                "part_id": current_id,
                "path": str(target.resolve()),
                "filename": target.name,
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "inline": is_inline,
            }
        )
    return saved


def _set_addresses(message: EmailMessage, header: str, values: list[str]) -> None:
    if values:
        message[header] = ", ".join(values)


def create_draft_message(
    *,
    sender: str,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body: str,
    body_format: str,
    attachments: list[Path] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Message-ID"] = make_msgid()
    message["From"] = sender
    _set_addresses(message, "To", to)
    _set_addresses(message, "Cc", cc or [])
    _set_addresses(message, "Bcc", bcc or [])
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    if body_format == "html":
        message.set_content(html_to_plain(body))
        message.add_alternative(body, subtype="html")
    elif body_format == "markdown":
        message.set_content(body)
    elif body_format == "plain":
        message.set_content(body)
    else:
        raise AppError("invalid_request", "draft body format must be html, markdown, or plain.")
    for attachment in attachments or []:
        ctype, _ = mimetypes.guess_type(str(attachment))
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        message.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
        )
    return message


def copy_attachment(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
