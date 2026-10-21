"""Build a MIME skeleton without retrieving attachment payloads."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from .errors import AppError
from .mime import parse_message
from .models import AttachmentInfo


def _text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def _at(value: Any, index: int) -> Any:
    return value[index] if len(value) > index else None


def _params(message: EmailMessage, header: str, values: Any) -> None:
    if not isinstance(values, (list, tuple)):
        return
    for index in range(0, len(values) - 1, 2):
        message.set_param(_text(values[index]).lower(), _text(values[index + 1]), header=header)


def _disposition(message: EmailMessage, value: Any) -> None:
    if isinstance(value, (list, tuple)) and value:
        message["Content-Disposition"] = _text(value[0])
        _params(message, "Content-Disposition", _at(value, 1))


def message_structure(headers: bytes, structure: Any) -> tuple[EmailMessage, list[tuple[str, EmailMessage, int | None]]]:
    """Accept both raw IMAP tuples and IMAPClient's normalized BodyData."""
    leaves: list[tuple[str, EmailMessage, int | None]] = []

    def build(value: Any, section: str) -> EmailMessage:
        if not isinstance(value, (tuple, list)) or not value:
            raise AppError("server_error", "The server returned an invalid MIME structure.", operation="read", phase="metadata_fetch")
        node = EmailMessage()
        if isinstance(value[0], (tuple, list)):
            if isinstance(value[0], list):
                children, subtype, extensions = value[0], _at(value, 1), value[2:]
            else:
                index = 0
                while index < len(value) and isinstance(value[index], (tuple, list)):
                    index += 1
                children, subtype, extensions = value[:index], _at(value, index), value[index + 1:]
            node.set_type("multipart/" + _text(subtype).lower())
            _params(node, "Content-Type", _at(extensions, 0))
            _disposition(node, _at(extensions, 1))
            node.set_payload([build(child, f"{section}.{i}" if section else str(i))
                              for i, child in enumerate(children, 1)])
            return node
        if len(value) < 7:
            raise AppError("server_error", "The server returned an incomplete MIME structure.", operation="read", phase="metadata_fetch")
        kind = f"{_text(value[0])}/{_text(value[1])}".lower()
        node.set_type(kind)
        _params(node, "Content-Type", value[2])
        if value[3]:
            node["Content-ID"] = _text(value[3])
        if value[5]:
            node["Content-Transfer-Encoding"] = _text(value[5])
        extension = 10 if kind == "message/rfc822" else 8 if kind.startswith("text/") else 7
        _disposition(node, _at(value, extension + 1))
        if kind == "message/rfc822" and _at(value, 8):
            nested = value[8]
            prefix = section or "1"
            # RFC 3501 section 6.4.5 gives embedded message bodies their own
            # part numbers. Multipart children add the number in build().
            nested_section = prefix if isinstance(nested[0], (tuple, list)) else prefix + ".1"
            node.set_payload([build(nested, nested_section)])
        else:
            node.set_payload("")
            # IMAP reports encoded octets. Decoded sizes are unknown for base64
            # and quoted-printable without downloading the attachment.
            size = int(value[6]) if value[6] is not None and _text(value[5]).lower() in {"7bit", "8bit", "binary"} else None
            leaves.append((section or "1", node, size))
        return node

    try:
        root = build(structure, "")
    except (IndexError, TypeError, ValueError, OverflowError):
        raise AppError("server_error", "The server returned an invalid MIME structure.", operation="read", phase="metadata_fetch") from None
    original = parse_message(headers)
    for name, value in root.raw_items():
        if name not in original:
            original[name] = value
    original.set_payload(root.get_payload())
    # Retain parsed headers directly, including malformed duplicate headers.
    leaves = [(section, original if node is root else node, size) for section, node, size in leaves]
    return original, leaves


def structure_attachments(leaves: list[tuple[str, EmailMessage, int | None]]) -> list[AttachmentInfo]:
    infos = []
    for _, node, size in leaves:
        filename = node.get_filename()
        disposition = node.get_content_disposition()
        if disposition not in {"attachment", "inline"} and not filename:
            continue
        index = str(len(infos) + 1)
        content_id = node.get("content-id")
        infos.append(AttachmentInfo(index, filename or f"part-{index}", node.get_content_type(), size,
                                    str(content_id).strip("<>") if content_id else None, disposition == "inline"))
    return infos


def body_part(leaves: list[tuple[str, EmailMessage, int | None]], body_format: str) -> tuple[str, EmailMessage] | None:
    preferred = ("text/plain", "text/html") if body_format == "plain" else ("text/html", "text/plain")
    for kind in preferred:
        for section, node, _ in leaves:
            if node.get_content_disposition() != "attachment" and node.get_content_type() == kind:
                return section, node
    return None
