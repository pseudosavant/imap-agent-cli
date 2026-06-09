from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_help_parser_has_expected_commands(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["search", "--subject", "invoice"])
        self.assertEqual(parsed.command, "search")
        self.assertEqual(parsed.subject, "invoice")

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
