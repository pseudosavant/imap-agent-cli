from __future__ import annotations

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.mime import (
    attachment_infos,
    create_draft_message,
    header_value,
    parse_message,
    render_body,
    safe_filename,
    save_attachments,
)


class MimeTests(unittest.TestCase):
    def test_render_html_body_is_sanitized(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Test"
        message.set_content("plain")
        message.add_alternative('<p>Hello</p><script>alert("x")</script>', subtype="html")
        body = render_body(message, "html", 1000)
        self.assertEqual(body["format"], "html")
        self.assertTrue(body["sanitized"])
        self.assertIn("Hello", body["content"])
        self.assertNotIn("<script", body["content"])

    def test_render_plain_falls_back_from_html(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Test"
        message.add_alternative("<p>Hello<br>World</p>", subtype="html")
        body = render_body(message, "plain", 1000)
        self.assertIn("Hello", body["content"])
        self.assertIn("World", body["content"])

    def test_attachment_metadata_and_save(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Attachment"
        message.set_content("See attached.")
        message.add_attachment(b"abc", maintype="text", subtype="plain", filename="../bad:name.txt")
        infos = attachment_infos(message)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].part_id, "1")
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_attachments(message, Path(tmp), part_id="1")
        self.assertEqual(saved[0]["filename"], "bad_name.txt")
        self.assertEqual(saved[0]["size_bytes"], 3)

    def test_create_draft_message_with_reply_headers(self) -> None:
        message = create_draft_message(
            sender="me@example.com",
            to=["you@example.com"],
            cc=[],
            bcc=[],
            subject="Re: Test",
            body="<p>Thanks</p>",
            body_format="html",
            in_reply_to="<source@example.com>",
            references="<old@example.com> <source@example.com>",
        )
        self.assertEqual(message["In-Reply-To"], "<source@example.com>")
        self.assertIn("<old@example.com>", message["References"])
        parsed = parse_message(message.as_bytes())
        self.assertEqual(parsed["Subject"], "Re: Test")

    def test_safe_filename(self) -> None:
        self.assertEqual(safe_filename("../x:y?.txt"), "x_y_.txt")
        self.assertEqual(safe_filename(".."), "attachment")

    def test_header_value_handles_malformed_message_id(self) -> None:
        raw = (
            b"Message-ID: <[8e15e2eaf6e3479a8a1c3d8230af2e58-JFZGS42DN5WW25LONFRWC5DJN5XFA3DBORTG64TNFVIHE33EFVGVOMKQPRAXU5LSMVCGK5SPOBZXYRLNMFUWY7CFPBXVG3LUOA======@microsoft.com]>\r\n"
            b"Subject: Test\r\n"
            b"\r\n"
            b"Body\r\n"
        )
        message = parse_message(raw)
        self.assertIn("@microsoft.com", header_value(message, "message-id"))
        self.assertEqual(header_value(message, "subject"), "Test")


if __name__ == "__main__":
    unittest.main()
