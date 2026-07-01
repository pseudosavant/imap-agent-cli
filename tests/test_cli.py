from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests import _bootstrap  # noqa: F401

from imap_agent_cli import __version__
from imap_agent_cli.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_help_parser_has_expected_commands(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["search", "--subject", "invoice"])
        self.assertEqual(parsed.command, "search")
        self.assertEqual(parsed.subject, "invoice")

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
