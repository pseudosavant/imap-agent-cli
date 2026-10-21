from __future__ import annotations

import base64
import imaplib
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import _bootstrap  # noqa: F401
from tests.test_imap_client import FakeServer

from imap_agent_cli.cli import main
from imap_agent_cli.errors import AppError
from imap_agent_cli.imap_client import ImapSession, READ_METADATA_FIELDS
from imap_agent_cli.mime_structure import message_structure, structure_attachments
from imap_agent_cli.models import Config, Defaults, Profile
from imap_agent_cli.render import write_error, write_json


UNICODE = "Telerik\u202fRen\u00e9e \u6771\u4eac \U0001f680"
PLAIN = (b"TEXT", b"PLAIN", (b"CHARSET", b"utf-8"), None, None, b"BASE64", 40, 1, None, None)
HTML = (b"TEXT", b"HTML", (b"CHARSET", b"utf-8"), None, None, b"QUOTED-PRINTABLE", 60, 1, None, None)
PDF = (b"APPLICATION", b"PDF", (b"NAME", b"report.pdf"), None, None, b"BASE64", 1000000,
       None, (b"ATTACHMENT", (b"FILENAME", b"report.pdf")))
STRUCTURE = ((PLAIN, HTML, b"ALTERNATIVE"), PDF, b"MIXED")
HEADERS = b"From: =?utf-8?b?UmVuw6ll?= <r@example.com>\r\nSubject: Example\r\nMessage-ID: <test@example.com>\r\nContent-Type: multipart/mixed\r\n\r\n"


def session_with(server: object) -> ImapSession:
    session = ImapSession(Profile(name="default", host="imap.example.com", username="demo@example.com", password="test-password"), Defaults())
    session.server = server
    return session


def search(session: ImapSession, **overrides):
    options = dict(folder="INBOX", scope="folder", subject=None, sender=None, recipient=None, message_id=None,
                   text=None, since=None, before=None, unseen=False, seen=False, answered=False, flagged=False,
                   larger=None, smaller=None, has_attachments=False, max_results=2, max_scan=100, sort="uid", order="desc")
    return session.search(**(options | overrides))


class UnicodeAndErrorTests(unittest.TestCase):
    def test_cp1252_captured_subprocess_success_error_and_utf8_stdin(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
import json, sys
from unittest.mock import patch
from imap_agent_cli.cli import main, _read_json_arg
from imap_agent_cli.errors import AppError
sys.stdout.reconfigure(encoding="cp1252")
sys.stderr.reconfigure(encoding="cp1252")
sys.stdin.reconfigure(encoding="cp1252")
assert sys.stdout.encoding == "cp1252"
def sync():
    assert sys.stdout.encoding == "utf-8"
    assert sys.stderr.encoding == "utf-8"
    assert sys.stdin.encoding == "utf-8"
def result():
    value = _read_json_arg("-")
    if sys.argv[1] == "error":
        raise AppError("server_error", value["text"])
    return value
with patch("imap_agent_cli.cli.sync_skill", side_effect=sync), patch("imap_agent_cli.cli._session") as factory:
    factory.return_value.__enter__.return_value.folders.side_effect = result
    raise SystemExit(main(["folders"]))
'''
        env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH", "IMAP_AGENT_CLI_NO_SKILL_SYNC"}}
        env["PYTHONPATH"] = str(root / "src")
        for mode in ("success", "error"):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-X", "utf8=0", "-c", program, mode], cwd=root / "src", env=env,
                                        input=json.dumps({"text": UNICODE}, ensure_ascii=False).encode("utf-8"), capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0 if mode == "success" else 1, result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)
                if mode == "success":
                    self.assertEqual(json.loads(result.stdout.decode("utf-8")), {"text": UNICODE})
                    self.assertEqual(result.stderr, b"")
                else:
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(json.loads(result.stderr.decode("utf-8"))["error"]["message"], UNICODE)

    def test_fixed_encoding_streams_and_stringio_preserve_values(self):
        for writer in (lambda: write_json({"text": UNICODE}), lambda: write_error(AppError("example", UNICODE))):
            for encoding in (None, "cp1252"):
                raw = BytesIO()
                stream = StringIO() if encoding is None else TextIOWrapper(raw, encoding=encoding)
                with patch("sys.stdout", stream), patch("sys.stderr", stream):
                    writer()
                value = stream.getvalue() if encoding is None else raw.getvalue().decode(encoding)
                payload = json.loads(value)
                self.assertEqual(payload.get("text") or payload["error"]["message"], UNICODE)

    def run_failure(self, exception: Exception, *flags: str):
        stdout, stderr = StringIO(), StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), patch("imap_agent_cli.cli.sync_skill"), patch("imap_agent_cli.cli._session", side_effect=exception):
            code = main(["folders", *flags])
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        return json.loads(stderr.getvalue())["error"]

    def test_unexpected_exceptions_are_redacted_and_debug_is_explicit(self):
        for flags in ((), ("--debug",)):
            error = self.run_failure(RuntimeError("credential token message-payload"), *flags)
            self.assertEqual(error["code"], "internal_error")
            self.assertNotIn("credential token message-payload", json.dumps(error))
            self.assertEqual("debug" in error, bool(flags))
            if flags:
                self.assertTrue(error["debug"]["traceback"])
                self.assertEqual(error["debug"]["exception_type"], "RuntimeError")

    def test_output_failure_is_structured(self):
        stdout = MagicMock()
        stdout.write.side_effect = OSError("do not disclose")
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), patch("imap_agent_cli.cli.sync_skill"):
            self.assertEqual(main(["--version"]), 1)
        # Direct help/version output failures are caught by the outer boundary.
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "output_failed")
        self.assertNotIn("do not disclose", stderr.getvalue())
        with patch("sys.stdout", stdout), patch("sys.stderr", StringIO()) as errors, patch("imap_agent_cli.cli.sync_skill"), patch("imap_agent_cli.cli._session") as factory:
            factory.return_value.__enter__.return_value.folders.return_value = {"folders": []}
            self.assertEqual(main(["folders"]), 1)
        self.assertEqual(json.loads(errors.getvalue())["error"]["code"], "output_failed")

    def test_invalid_arguments_and_invalid_utf8_are_json_errors(self):
        for args, stdin in ((["read", "--uid", "invalid-secret"], StringIO()),
                            (["search", "--json", "-"], TextIOWrapper(BytesIO(b"\xff"), encoding="cp1252"))):
            with self.subTest(args=args), patch("sys.stderr", StringIO()) as stderr, patch("sys.stdin", stdin), patch("imap_agent_cli.cli.sync_skill"):
                self.assertNotEqual(main(args), 0)
            error = json.loads(stderr.getvalue())["error"]
            self.assertEqual(error["code"], "invalid_request")
            self.assertNotIn("invalid-secret", stderr.getvalue())

    def test_serialization_does_not_replace_invalid_utf8(self):
        with patch("sys.stdout", StringIO()), self.assertRaises(AppError) as caught:
            write_json({"bytes": b"\xff"})
        self.assertEqual(caught.exception.code, "output_failed")

    def test_closed_output_pipe_has_no_shutdown_traceback(self):
        root = Path(__file__).resolve().parents[1]
        program = 'from imap_agent_cli.cli import main\nraise SystemExit(main(["--no-skill-sync", "--help"]))'
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        with subprocess.Popen([sys.executable, "-c", program], cwd=root / "src", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            child.stdout.close()
            stderr = child.stderr.read()
            self.assertEqual(child.wait(timeout=15), 1, stderr)
        self.assertNotIn(b"Traceback", stderr)
        self.assertNotIn(b"Exception ignored", stderr)
        self.assertEqual(json.loads(stderr)["error"]["code"], "output_failed")

    def test_sync_can_be_disabled_on_commands_and_help(self):
        for args in ([], ["--help"], ["--version"], ["--about"], ["read", "--help"], ["read", "--uid", "1"]):
            for placement in ("prefix", "suffix", "environment"):
                argv = ["--no-skill-sync", *args] if placement == "prefix" else [*args, "--no-skill-sync"] if placement == "suffix" else args
                with patch.dict(os.environ, {"IMAP_AGENT_CLI_NO_SKILL_SYNC": "1" if placement == "environment" else "0"}), patch("sys.stdout", StringIO()), patch("sys.stderr", StringIO()), patch("imap_agent_cli.cli.sync_skill") as sync:
                    main(argv)
                sync.assert_not_called()


class ReadTests(unittest.TestCase):
    def fixture(self, structure=STRUCTURE):
        server = MagicMock()
        def fetch(uids, fields):
            if fields == READ_METADATA_FIELDS:
                return {1: {b"BODY[HEADER]": HEADERS, b"BODYSTRUCTURE": structure}}
            if fields == ["BODY.PEEK[1.1]"]:
                return {1: {b"BODY[1.1]": base64.b64encode(UNICODE.encode("utf-8"))}}
            if fields == ["BODY.PEEK[1.2]"]:
                return {1: {b"BODY[1.2]": b"<p>Ren=C3=A9e</p><script>bad()</script>"}}
            self.fail(f"Unexpected fetch: {fields}")
        server.fetch.side_effect = fetch
        return session_with(server), server

    def read(self, session, body_format, include="metadata"):
        return session.read(folder="INBOX", uid=1, body_format=body_format, max_body_chars=1000, include_attachments=include)

    def test_metadata_only_uses_headers_and_structure(self):
        session, server = self.fixture()
        payload = self.read(session, "metadata")
        server.select_folder.assert_called_once_with("INBOX", readonly=True)
        server.fetch.assert_called_once_with([1], READ_METADATA_FIELDS)
        self.assertEqual(payload["body"]["content"], "")
        self.assertEqual(payload["headers"]["from"][0]["name"], "Ren\u00e9e")
        self.assertEqual(payload["attachment_count"], 1)
        self.assertEqual(payload["attachments"][0].filename, "report.pdf")
        self.assertIsNone(payload["attachments"][0].size_bytes)
        server.append.assert_not_called()

    def test_plain_body_fetches_only_selected_part_and_decodes_mime(self):
        session, server = self.fixture()
        payload = self.read(session, "plain", "none")
        self.assertEqual(payload["body"]["content"], UNICODE)
        self.assertEqual(server.fetch.call_count, 2)
        self.assertEqual(server.fetch.call_args.args[1], ["BODY.PEEK[1.1]"])
        self.assertNotIn("attachments", payload)
        self.assertTrue(payload["has_attachments"])

    def test_html_markdown_and_raw_html_choose_html(self):
        for body_format in ("html", "markdown", "raw-html"):
            with self.subTest(body_format=body_format):
                session, server = self.fixture()
                payload = self.read(session, body_format)
                self.assertIn("Ren\u00e9e", payload["body"]["content"])
                self.assertEqual(server.fetch.call_args.args[1], ["BODY.PEEK[1.2]"])
                if body_format != "raw-html":
                    self.assertNotIn("<script>", payload["body"]["content"])

    def test_plain_falls_back_to_html_with_no_plain_part(self):
        session, server = self.fixture((HTML, PDF, b"MIXED"))
        server.fetch.side_effect = [{1: {b"BODY[HEADER]": HEADERS, b"BODYSTRUCTURE": (HTML, PDF, b"MIXED")}},
                                    {1: {b"BODY[1]": b"<p>Hello</p>"}}]
        self.assertEqual(self.read(session, "plain")["body"]["content"], "Hello")
        self.assertEqual(server.fetch.call_args.args[1], ["BODY.PEEK[1]"])

    def test_singlepart_body_and_returned_character_limit(self):
        session, server = self.fixture(PLAIN)
        server.fetch.side_effect = [{1: {b"BODY[HEADER]": b"Subject: Single\r\n", b"BODYSTRUCTURE": PLAIN}},
                                    {1: {b"BODY[1]": base64.b64encode(UNICODE.encode("utf-8"))}}]
        result = session.read(folder="INBOX", uid=1, body_format="plain", max_body_chars=3, include_attachments="none")
        self.assertEqual(result["body"]["content"], UNICODE[:3])
        self.assertTrue(result["body"]["truncated"])
        self.assertEqual(server.fetch.call_args.args[1], ["BODY.PEEK[1]"])

    def test_normalized_bodydata_inline_filename_and_rfc2231(self):
        from imapclient.response_types import BodyData
        inline = (b"IMAGE", b"PNG", None, b"<image1>", None, b"8BIT", 12, None,
                  (b"INLINE", (b"FILENAME*", b"utf-8''caf%C3%A9.png")))
        message, leaves = message_structure(HEADERS, BodyData.create((PLAIN, inline, b"MIXED")))
        info = structure_attachments(leaves)[0]
        self.assertEqual(info.filename, "caf\u00e9.png")
        self.assertEqual(info.content_id, "image1")
        self.assertEqual(info.part_id, "1")
        self.assertEqual(info.size_bytes, 12)
        self.assertTrue(info.inline)
        self.assertTrue(message.is_multipart())

    def test_attachment_listing_is_metadata_only(self):
        session, server = self.fixture()
        self.assertEqual(session.attachments(folder="INBOX", uid=1)["attachments"][0].filename, "report.pdf")
        server.fetch.assert_called_once_with([1], READ_METADATA_FIELDS)

    def test_embedded_messages_have_nested_mime_part_numbers(self):
        for nested, expected in ((PLAIN, "2.1"), ((PLAIN, HTML, b"ALTERNATIVE"), "2.1")):
            embedded = (b"MESSAGE", b"RFC822", None, None, None, b"7BIT", 200, (), nested, 2, None, None)
            _, leaves = message_structure(HEADERS, (PDF, embedded, b"MIXED"))
            self.assertEqual(leaves[1][0], expected)

    def test_duplicate_headers_and_legacy_charset_are_preserved(self):
        legacy = (b"TEXT", b"PLAIN", (b"CHARSET", b"iso-8859-1"), None, None, b"QUOTED-PRINTABLE", 10, 1)
        message, leaves = message_structure(b"Subject: First\r\nSubject: Second\r\n", legacy)
        leaves[0][1].set_payload(b"Ren=E9e")
        from imap_agent_cli.mime import render_body
        self.assertEqual(render_body(message, "plain", 100)["content"], "Ren\u00e9e")
        self.assertEqual(message.get_all("Subject"), ["First", "Second"])

    def test_thread_headers_and_latest_body_avoid_full_message_fetch(self):
        from imap_agent_cli.imap_client import THREAD_HEADER_FETCH
        for include in ("none", "latest"):
            session, server = self.fixture()
            server.search.return_value = [1]
            original_fetch = server.fetch.side_effect
            def fetch(uids, fields):
                if fields[0] == THREAD_HEADER_FETCH:
                    return {1: {b"BODY[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES DATE FROM TO CC SUBJECT)]": HEADERS}}
                return original_fetch(uids, fields)
            server.fetch.side_effect = fetch
            result = session.thread(folder="INBOX", uid=1, max_messages=5, max_scan=10,
                                    include_body=include, body_format="plain", max_body_chars=1000)
            self.assertEqual(len(result["messages"]), 1)
            self.assertEqual("body" in result["messages"][0], include == "latest")
            if include == "latest":
                self.assertEqual(result["messages"][0]["body"]["content"], UNICODE)
            self.assertTrue(all("BODY.PEEK[]" not in call.args[1] for call in server.fetch.call_args_list))

    def test_missing_message_and_missing_fetch_values_are_distinct(self):
        for response, code in (({}, "message_not_found"), ({1: {b"FLAGS": ()}}, "server_error")):
            session, server = self.fixture()
            server.fetch.side_effect = None
            server.fetch.return_value = response
            with self.assertRaises(AppError) as caught:
                self.read(session, "metadata")
            self.assertEqual(caught.exception.code, code)

    def test_malformed_structure_is_a_redacted_server_error(self):
        invalid = (*PDF[:5], b"8BIT", "secret invalid size", *PDF[7:])
        session, server = self.fixture(invalid)
        with self.assertRaises(AppError) as caught:
            self.read(session, "metadata")
        self.assertEqual(caught.exception.code, "server_error")
        self.assertNotIn("secret invalid size", caught.exception.message)


class RecoveryTests(unittest.TestCase):
    def fixture(self, failure):
        first, second = MagicMock(), MagicMock()
        first.search.side_effect = failure
        second.search.return_value = [1]
        second.fetch.return_value = FakeServer().fetch([1], [])
        return session_with(first), first, second

    def test_timeout_reconnects_reselects_and_retries_search_once(self):
        session, first, second = self.fixture(TimeoutError("secret response"))
        with patch("imapclient.IMAPClient", return_value=second) as connection:
            result = search(session)
        self.assertEqual([item["uid"] for item in result["results"]], [1])
        first.shutdown.assert_called_once()
        first.logout.assert_not_called()
        first.search.assert_called_once()
        second.search.assert_called_once()
        second.select_folder.assert_called_once_with("INBOX", readonly=True)
        second.login.assert_called_once_with("demo@example.com", "test-password")
        connection.assert_called_once()

    def test_repeated_timeout_is_bounded_and_identifies_phase(self):
        session, first, second = self.fixture(TimeoutError("secret response"))
        second.search.side_effect = TimeoutError("secret response")
        with patch("imapclient.IMAPClient", return_value=second) as connection, self.assertRaises(AppError) as caught:
            search(session)
        error = caught.exception
        self.assertEqual(error.code, "connection_timeout")
        self.assertTrue(error.retryable)
        self.assertEqual(error.details, {"operation": "search", "phase": "search", "attempts": 2})
        self.assertNotIn("secret response", error.message)
        connection.assert_called_once()
        second.shutdown.assert_called_once()

    def test_wrapped_socket_timeout_keeps_timeout_classification(self):
        try:
            try:
                raise TimeoutError("private server response")
            except TimeoutError as cause:
                raise imaplib.IMAP4.abort("connection closed") from cause
        except imaplib.IMAP4.abort as failure:
            session, first, second = self.fixture(failure)
            second.search.side_effect = failure
            with patch("imapclient.IMAPClient", return_value=second), self.assertRaises(AppError) as caught:
                search(session)
        self.assertEqual(caught.exception.code, "connection_timeout")
        self.assertNotIn("private server response", caught.exception.message)

    def test_fetch_network_server_and_missing_errors_are_distinct(self):
        for failure, expected, attempts in ((TimeoutError(), "connection_timeout", 2),
                                            (imaplib.IMAP4.abort(), "connection_failed", 2),
                                            (imaplib.IMAP4.error("secret"), "server_error", 1)):
            first, second = MagicMock(), MagicMock()
            first.fetch.side_effect = failure
            second.fetch.side_effect = failure
            session = session_with(first)
            with patch("imapclient.IMAPClient", return_value=second) as connection, self.assertRaises(AppError) as caught:
                session._fetch_raw("INBOX", 1)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(caught.exception.retryable, attempts == 2)
            self.assertEqual(caught.exception.details["phase"], "body_fetch")
            self.assertEqual(caught.exception.details["attempts"], attempts)
            self.assertEqual(connection.call_count, attempts - 1)
        server = MagicMock()
        server.fetch.return_value = {}
        with self.assertRaises(AppError) as caught:
            session_with(server)._fetch_raw("INBOX", 1)
        self.assertEqual(caught.exception.code, "message_not_found")

    def test_metadata_fetch_retry_restores_readonly_selection(self):
        first, second = MagicMock(), MagicMock()
        first.fetch.side_effect = TimeoutError()
        second.fetch.return_value = {1: {b"BODY[HEADER]": HEADERS, b"BODYSTRUCTURE": STRUCTURE}}
        with patch("imapclient.IMAPClient", return_value=second):
            session_with(first).read(folder="INBOX", uid=1, body_format="metadata", max_body_chars=100, include_attachments="none")
        second.select_folder.assert_called_once_with("INBOX", readonly=True)
        second.fetch.assert_called_once_with([1], READ_METADATA_FIELDS)

    def test_body_retry_fetches_the_same_text_part_without_attachments(self):
        first, second = MagicMock(), MagicMock()
        first.fetch.side_effect = [{1: {b"BODY[HEADER]": HEADERS, b"BODYSTRUCTURE": STRUCTURE}}, TimeoutError()]
        second.fetch.return_value = {1: {b"BODY[1.1]": base64.b64encode(UNICODE.encode("utf-8"))}}
        with patch("imapclient.IMAPClient", return_value=second):
            result = session_with(first).read(folder="INBOX", uid=1, body_format="plain", max_body_chars=1000, include_attachments="none")
        second.fetch.assert_called_once_with([1], ["BODY.PEEK[1.1]"])
        self.assertEqual(result["body"]["content"], UNICODE)

    def test_reconnect_authentication_failure_stops_without_more_retries(self):
        session, first, second = self.fixture(TimeoutError())
        second.login.side_effect = imaplib.IMAP4.error("secret")
        with patch("imapclient.IMAPClient", return_value=second) as connection, self.assertRaises(AppError) as caught:
            search(session)
        self.assertEqual(caught.exception.code, "auth_failed")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.details["phase"], "reconnect")
        connection.assert_called_once()
        second.search.assert_not_called()

    def test_append_timeout_is_never_retried_and_reports_unknown_outcome(self):
        server = MagicMock()
        server.append.side_effect = TimeoutError("secret append payload")
        with patch("imapclient.IMAPClient") as connection, self.assertRaises(AppError) as caught:
            session_with(server).append_draft(EmailMessage(), drafts_folder="Drafts")
        server.append.assert_called_once()
        connection.assert_not_called()
        self.assertEqual(caught.exception.code, "connection_timeout")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.details["outcome"], "unknown")
        self.assertNotIn("secret append payload", caught.exception.message)

    def test_login_timeout_has_phase_and_does_not_become_auth_failure(self):
        server = MagicMock()
        server.login.side_effect = TimeoutError("secret")
        with patch("imapclient.IMAPClient", return_value=server), self.assertRaises(AppError) as caught:
            session_with(None).__enter__()
        self.assertEqual(caught.exception.code, "connection_timeout")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.details["phase"], "login")

    def test_drafts_folder_fallback_checks_all_candidates(self):
        session = session_with(None)
        with patch.object(session, "folders", return_value={"folders": [{"name": "INBOX.Drafts"}]}):
            self.assertEqual(session.resolve_drafts_folder(), "INBOX.Drafts")


class SearchEfficiencyTests(unittest.TestCase):
    def fixture(self):
        server = MagicMock()
        server.search.return_value = list(range(1, 101))
        server.fetch.side_effect = FakeServer().fetch
        return session_with(server), server

    def test_uid_early_stop_asc_and_desc(self):
        for order, expected in (("desc", [100, 99]), ("asc", [1, 2])):
            session, server = self.fixture()
            result = search(session, order=order)
            self.assertEqual([item["uid"] for item in result["results"]], expected)
            self.assertEqual(server.fetch.call_args.args[0], expected)
            server.fetch.assert_called_once()
            self.assertEqual(result["scanned_count"], 2)
            self.assertTrue(result["truncated"])

    def test_attachment_filter_continues_until_enough_matches(self):
        session, server = self.fixture()
        server.search.return_value = [1, 2, 3, 4]
        def fetch(uids, fields):
            data = FakeServer().fetch(uids, fields)
            for uid in uids:
                data[uid]["BODYSTRUCTURE"] = (PLAIN, PDF, b"MIXED") if uid in {2, 3} else PLAIN
            return data
        server.fetch.side_effect = fetch
        result = search(session, has_attachments=True)
        self.assertEqual([item["uid"] for item in result["results"]], [3, 2])
        self.assertEqual([call.args[0] for call in server.fetch.call_args_list], [[4, 3], [2]])
        self.assertEqual(result["scanned_count"], 3)

    def test_uid_fetch_batches_do_not_overrun_result_limit(self):
        session, server = self.fixture()
        result = search(session, max_results=60)
        self.assertEqual([len(call.args[0]) for call in server.fetch.call_args_list], [50, 10])
        self.assertEqual(len(result["results"]), 60)
        self.assertEqual(result["scanned_count"], 60)

    def test_date_sort_scans_bound_and_sorts_across_folders_and_offsets(self):
        session, server = self.fixture()
        session._folder_names_for_scope = MagicMock(return_value=["INBOX", "Other"])
        server.search.return_value = [1, 2, 3]
        def fetch(uids, fields):
            data = FakeServer().fetch(uids, fields)
            selected = server.select_folder.call_args.args[0]
            for uid in uids:
                data[uid]["INTERNALDATE"] = ("2026-01-01T11:00:00+02:00" if uid == 2 else "2026-01-01T10:00:00+00:00") if selected == "INBOX" else "2026-01-02T00:00:00+00:00"
            return data
        server.fetch.side_effect = fetch
        result = search(session, scope="all", sort="date", max_scan=2, max_results=3)
        self.assertEqual([(item["folder"], item["uid"]) for item in result["results"]], [("Other", 3), ("Other", 2), ("INBOX", 3)])
        self.assertEqual(result["scanned_count"], 4)
        self.assertTrue(result["truncated"])
        self.assertEqual([call.args[0] for call in server.fetch.call_args_list], [[3, 2], [3, 2]])

    def test_date_order_and_attachment_filter_do_not_stop_at_first_match(self):
        session, server = self.fixture()
        server.search.return_value = [1, 2, 3]
        def fetch(uids, fields):
            data = FakeServer().fetch(uids, fields)
            for uid in uids:
                data[uid]["INTERNALDATE"] = datetime(2026, 1, 4 - uid, tzinfo=timezone.utc)
                data[uid]["BODYSTRUCTURE"] = (PLAIN, PDF, b"MIXED")
            return data
        server.fetch.side_effect = fetch
        result = search(session, sort="date", max_results=1, has_attachments=True)
        self.assertEqual(result["results"][0]["uid"], 1)
        self.assertEqual(result["scanned_count"], 3)

    def test_timings_are_opt_in_json_and_do_not_expose_search_terms(self):
        session, _ = self.fixture()
        for enabled in (False, True):
            session.timings = enabled
            with patch("sys.stderr", StringIO()) as stderr:
                search(session, subject="secret query")
            diagnostics = [json.loads(line)["diagnostic"] for line in stderr.getvalue().splitlines()]
            self.assertEqual(bool(diagnostics), enabled)
            self.assertNotIn("secret query", stderr.getvalue())
            if enabled:
                self.assertIn("search", {item["phase"] for item in diagnostics})
                self.assertIn("metadata_fetch", {item["phase"] for item in diagnostics})


class MinimalOutputTests(unittest.TestCase):
    def test_default_minimal_and_selected_fields(self):
        full = {"profile": "default", "query": {"subject": None}, "results": [{"folder": "INBOX", "uid": 1, "subject": UNICODE, "from": [], "date": "today", "flags": []}], "scanned_count": 1}
        for flags, request in (([], {}), (["--minimal"], {}), (["--fields", "uid,subject"], {}),
                               (["--json", "-"], {"minimal": True, "fields": ["folder", "uid"]})):
            with patch("imap_agent_cli.cli.sync_skill"), patch("imap_agent_cli.cli.load_config", return_value=Config()), patch("imap_agent_cli.cli._session") as factory, patch("sys.stdout", StringIO()) as stdout, patch("sys.stdin", StringIO(json.dumps(request))):
                factory.return_value.__enter__.return_value.search.return_value = {**full, "results": [dict(full["results"][0])]}
                self.assertEqual(main(["search", *flags]), 0)
            result = json.loads(stdout.getvalue())
            if not flags:
                self.assertEqual(result, full)
            elif "--minimal" in flags:
                self.assertNotIn("query", result)
                self.assertEqual(set(result["results"][0]), {"folder", "uid", "subject", "from", "date"})
            elif "--fields" in flags:
                self.assertEqual(set(result["results"][0]), {"uid", "subject"})
                self.assertIn("query", result)
            else:
                self.assertNotIn("query", result)
                self.assertEqual(set(result["results"][0]), {"folder", "uid"})

    def test_invalid_field_selection_fails_before_connecting(self):
        with patch("imap_agent_cli.cli._session") as factory, patch("imap_agent_cli.cli.sync_skill"), patch("sys.stderr", StringIO()) as stderr:
            self.assertEqual(main(["search", "--fields", "password"]), 1)
        factory.assert_not_called()
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "invalid_request")
