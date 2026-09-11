from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests import _bootstrap  # noqa: F401

from imap_agent_cli import __version__
from imap_agent_cli.cli import build_parser, main
from imap_agent_cli.models import Config, Defaults, Profile


class FakeCheckSession:
    def __init__(self, profile: Profile, defaults: Defaults) -> None:
        self.profile = profile
        self.defaults = defaults
        self.server = MagicMock()
        self.server.list_folders.return_value = [([], "/", "INBOX"), ([b"\\Drafts"], "/", "Drafts")]
        self.security = {"ssl_mode": profile.ssl_mode, "encrypted": True, "method": "implicit_tls"}

    def __enter__(self) -> "FakeCheckSession":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def capabilities(self) -> list[str]:
        return ["IMAP4REV1"]

    def folders(self) -> dict[str, object]:
        return {
            "folders": [
                {"name": "INBOX", "selectable": True, "special_use": "inbox"},
                {"name": "Drafts", "selectable": True, "special_use": "drafts"},
            ]
        }

    def _select(self, folder: str, *, readonly: bool = True) -> dict:
        return {b"UIDNEXT": 1}

    def resolve_drafts_folder(self) -> str:
        return "Drafts"


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("imap_agent_cli.skill.default_skills_dir", return_value=Path(directory)))
        self.enterContext(patch("imap_agent_cli.skill.is_local_development", return_value=True))

    def test_help_parser_has_expected_commands(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["search", "--subject", "invoice"])
        self.assertEqual(parsed.command, "search")
        self.assertEqual(parsed.subject, "invoice")

    def test_drafts_use_saved_sender_with_non_email_login(self) -> None:
        for args in (["draft", "create", "--to", "recipient@example.com"],
                     ["draft", "reply", "--folder", "INBOX", "--uid", "1"]):
            with self.subTest(args=args), patch("imap_agent_cli.cli.load_config", return_value=Config()), patch("imap_agent_cli.cli._session") as factory, patch("sys.stdout", StringIO()):
                session = factory.return_value.__enter__.return_value
                session.profile = Profile(name="default", host="imap.example.com", username="login123", sender="sender@example.com")
                session._fetch_raw.return_value = b"From: recipient@example.com\r\nSubject: Example\r\nMessage-ID: <original@example.com>\r\n\r\nBody"
                session.append_draft.return_value = {"created": True}
                self.assertEqual(main([*args, "--body", "Draft text"]), 0)
                self.assertEqual(session.append_draft.call_args.args[0]["From"], "sender@example.com")

    def test_search_scope_defaults_and_explicit_overrides(self) -> None:
        cases = [
            ([], None, Defaults(), "INBOX", "folder"),
            ([], {"subject": "invoice"}, Defaults(), "INBOX", "folder"),
            (["--folder", "Archive/Support"], None, Defaults(), "Archive/Support", "folder"),
            (["--folder", "Projects", "--recursive"], None, Defaults(), "Projects", "recursive"),
            (["--all-folders"], None, Defaults(), "INBOX", "all"),
            ([], {"folder": "Archive/Support"}, Defaults(), "Archive/Support", "folder"),
            ([], {"folder": "Projects", "scope": "recursive"}, Defaults(), "Projects", "recursive"),
            ([], {"scope": "all"}, Defaults(), "INBOX", "all"),
            ([], None, Defaults(default_folder="Projects"), "Projects", "folder"),
            (["--folder", "INBOX"], None, Defaults(default_folder="Projects"), "INBOX", "folder"),
        ]
        for flags, payload, defaults, folder, scope in cases:
            with self.subTest(flags=flags, payload=payload, default_folder=defaults.default_folder):
                stdout = StringIO()
                with (
                    patch("imap_agent_cli.cli.load_config", return_value=Config(defaults=defaults)),
                    patch("imap_agent_cli.cli._session") as session_factory,
                    patch("sys.stdout", stdout),
                    patch("sys.stdin", StringIO(json.dumps(payload))),
                ):
                    session = session_factory.return_value.__enter__.return_value
                    session.search.return_value = {"results": []}
                    args = ["search", *flags]
                    if payload is not None:
                        args.extend(["--json", "-"])
                    code = main(args)
                self.assertEqual(code, 0)
                session.search.assert_called_once()
                self.assertEqual(session.search.call_args.kwargs["folder"], folder)
                self.assertEqual(session.search.call_args.kwargs["scope"], scope)
                self.assertEqual(json.loads(stdout.getvalue()), {"results": []})

    def test_no_args_prints_agent_quick_reference(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main([])
        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("safe IMAP email access for agentic tools", output)
        self.assertIn("Safety:", output)
        self.assertIn("IMAP_AGENT_CLI_HOST", output)
        self.assertIn("imap-agent-cli search --folder INBOX", output)
        self.assertIn("stdout is JSON payload only", output)
        self.assertIn("https://github.com/pseudosavant/imap-agent-cli", output)
        self.assertIn("License:\n  MIT", output)

    def test_top_level_help_prints_agent_quick_reference(self) -> None:
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            code = main(["--help"])
        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Common workflows:", output)
        self.assertIn("imap-agent-cli <command> --help", output)

    def test_about_prints_project_and_license(self) -> None:
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            code = main(["--about"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue(),
            f"""imap-agent-cli {__version__}

Safe IMAP email access for agentic tools.

Project: https://github.com/pseudosavant/imap-agent-cli
License: MIT
""",
        )

    def test_version_prints_only_semver(self) -> None:
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), f"{__version__}\n")

    def test_config_init_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            with patch("imap_agent_cli.cli.init_config", return_value=config_path):
                stdout = StringIO()
                with patch("sys.stdout", stdout):
                    code = main(["config", "init"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["created"])
        self.assertEqual(payload["path"], str(config_path))

    def test_config_check_outputs_diagnostics_json(self) -> None:
        config = Config(
            defaults=Defaults(),
            profiles={
                "default": Profile(
                    name="default",
                    host="imap.example.com",
                    username="me@example.com",
                    password="secret",
                )
            },
        )
        stdout = StringIO()
        with (
            patch("imap_agent_cli.cli.load_config", return_value=config),
            patch("imap_agent_cli.cli.ImapSession", FakeCheckSession),
            patch("sys.stdout", stdout),
        ):
            code = main(["config", "check"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["profile"]["host"], "imap.example.com")
        self.assertIn("login", {item["name"] for item in payload["checks"]})

    def test_read_missing_folder_errors_to_stderr(self) -> None:
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            code = main(["read", "--uid", "1"])
        self.assertEqual(code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_attachments_missing_folder_errors_to_stderr(self) -> None:
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            code = main(["attachments"])
        self.assertEqual(code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
