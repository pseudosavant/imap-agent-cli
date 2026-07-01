from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.errors import AppError
from imap_agent_cli.imap_client import ImapSession, message_payload
from imap_agent_cli.mime import save_attachments
from imap_agent_cli.models import Defaults, Profile


@dataclass
class FakeAddress:
    name: bytes
    mailbox: bytes
    host: bytes


@dataclass
class FakeEnvelope:
    subject: bytes
    date: str
    from_: list[FakeAddress]
    to: list[FakeAddress]
    cc: list[FakeAddress]
    message_id: bytes


class FakeServer:
    def __init__(self) -> None:
        self.fetch_fields: list[Any] = []

    def list_folders(self) -> list[tuple[list[str], str, str]]:
        return [
            ([], "/", "Projects"),
            ([], "/", "Projects/Child"),
            ([], "/", "ProjectsOld"),
            (["\\Noselect"], "/", "Projects/Archive"),
            ([], "/", "Junk"),
        ]

    def folder_status(self, name: str, fields: list[str]) -> dict[str, int]:
        return {"MESSAGES": 0, "UNSEEN": 0}

    def select_folder(self, folder: str, readonly: bool = False) -> None:
        return None

    def search(self, criteria: list[Any]) -> list[int]:
        return [1, 2]

    def fetch(self, uids: list[int], fields: list[str]) -> dict[int, dict[str, Any]]:
        self.fetch_fields.append(fields)
        return {
            uid: {
                "ENVELOPE": FakeEnvelope(
                    subject=f"Subject {uid}".encode(),
                    date=f"Tue, 30 Jun 2026 10:0{uid}:00 -0700",
                    from_=[FakeAddress(b"Sender", b"sender", b"example.com")],
                    to=[FakeAddress(b"Recipient", b"recipient", b"example.com")],
                    cc=[],
                    message_id=f"<{uid}@example.com>".encode(),
                ),
                "INTERNALDATE": f"2026-06-30T10:0{uid}:00-07:00",
                "RFC822.SIZE": 100 + uid,
                "BODYSTRUCTURE": ("TEXT", "PLAIN", ("ATTACHMENT" if uid == 2 else "")),
                "FLAGS": (),
            }
            for uid in uids
        }


class ImapClientTests(unittest.TestCase):
    def _session(self) -> ImapSession:
        session = ImapSession(Profile(name="default", host="imap.example.com", username="me@example.com"), Defaults())
        session.server = FakeServer()
        return session

    def test_recursive_scope_uses_delimiter(self) -> None:
        session = self._session()
        self.assertEqual(session._folder_names_for_scope("Projects", "recursive"), ["Projects", "Projects/Child"])

    def test_all_scope_excludes_junk_by_name(self) -> None:
        session = self._session()
        self.assertNotIn("Junk", session._folder_names_for_scope("INBOX", "all"))

    def test_search_fetches_metadata_instead_of_raw_messages(self) -> None:
        session = self._session()
        payload = session.search(
            folder="INBOX",
            scope="folder",
            subject=None,
            sender=None,
            recipient=None,
            message_id=None,
            text=None,
            since=None,
            before=None,
            unseen=False,
            seen=False,
            answered=False,
            flagged=False,
            larger=None,
            smaller=None,
            has_attachments=True,
            max_results=10,
            max_scan=10,
            sort="uid",
            order="desc",
        )
        self.assertEqual([item["uid"] for item in payload["results"]], [2])
        self.assertEqual(session.server.fetch_fields[0], ["ENVELOPE", "INTERNALDATE", "RFC822.SIZE", "BODYSTRUCTURE", "FLAGS"])

    def test_message_payload_can_omit_attachment_metadata(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Attachment"
        message.set_content("See attached.")
        message.add_attachment(b"abc", maintype="text", subtype="plain", filename="file.txt")
        payload = message_payload(
            profile="default",
            folder="INBOX",
            uid=1,
            message=message,
            body_format="metadata",
            max_body_chars=1000,
            include_attachments="none",
        )
        self.assertTrue(payload["has_attachments"])
        self.assertEqual(payload["attachment_count"], 1)
        self.assertNotIn("attachments", payload)

    def test_attachment_save_preflights_duplicate_targets(self) -> None:
        message = EmailMessage()
        message.set_content("See attached.")
        message.add_attachment(b"one", maintype="text", subtype="plain", filename="same.txt")
        message.add_attachment(b"two", maintype="text", subtype="plain", filename="same.txt")
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with self.assertRaises(AppError):
                save_attachments(message, output_dir, all_parts=True)
            self.assertFalse((output_dir / "same.txt").exists())


if __name__ == "__main__":
    unittest.main()
