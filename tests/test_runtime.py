from __future__ import annotations

import json
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import Mock, patch

from tests import _bootstrap  # noqa: F401

from imap_agent_cli import runtime


class RuntimeTests(unittest.TestCase):
    def distribution(self, source: dict | None = None) -> Mock:
        distribution = Mock()
        distribution.files = [Path("imap_agent_cli/runtime.py")]
        distribution.locate_file.return_value = Path(runtime.__file__)
        distribution.read_text.return_value = json.dumps(source) if source is not None else None
        return distribution

    def test_index_and_wheel_installs_are_eligible(self) -> None:
        for source in (
            None,
            {"url": "file:///tmp/imap_agent_cli-0.1.6-py3-none-any.whl", "archive_info": {"hashes": {}}},
            {"url": "https://example.com/imap_agent_cli-0.1.6-py3-none-any.whl", "archive_info": {}},
        ):
            with self.subTest(source=source), patch("imap_agent_cli.runtime.metadata.distribution", return_value=self.distribution(source)):
                self.assertFalse(runtime.is_local_development())

    def test_local_directories_editables_and_source_archives_are_excluded(self) -> None:
        for source in (
            {"url": "file:///tmp/repo", "dir_info": {}},
            {"url": "file:///tmp/repo", "dir_info": {"editable": True}},
            {"url": "file:///tmp/repo.tar.gz", "archive_info": {}},
            {"url": "file:///tmp/repo"},
        ):
            with self.subTest(source=source), patch("imap_agent_cli.runtime.metadata.distribution", return_value=self.distribution(source)):
                self.assertTrue(runtime.is_local_development())

    def test_checkout_shadowing_an_installed_distribution_is_excluded(self) -> None:
        distribution = self.distribution()
        distribution.locate_file.return_value = Path(runtime.__file__).parent / "other" / "runtime.py"
        with patch("imap_agent_cli.runtime.metadata.distribution", return_value=distribution):
            self.assertTrue(runtime.is_local_development())

    def test_unknown_or_malformed_distribution_metadata_is_excluded(self) -> None:
        with patch("imap_agent_cli.runtime.metadata.distribution", side_effect=metadata.PackageNotFoundError):
            self.assertTrue(runtime.is_local_development())
        for direct_url in ("not json", "{}", "[]", '{"url": null}', '{"url": "relative/path"}'):
            distribution = self.distribution()
            distribution.read_text.return_value = direct_url
            with self.subTest(direct_url=direct_url), patch("imap_agent_cli.runtime.metadata.distribution", return_value=distribution):
                self.assertTrue(runtime.is_local_development())
        distribution = self.distribution()
        distribution.files = None
        with patch("imap_agent_cli.runtime.metadata.distribution", return_value=distribution):
            self.assertTrue(runtime.is_local_development())

    def test_actual_checkout_is_development(self) -> None:
        self.assertTrue(runtime.is_local_development())
