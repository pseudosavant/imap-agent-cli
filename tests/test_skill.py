from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.cli import main
from imap_agent_cli.errors import AppError
from imap_agent_cli.skill import MANAGED_MARKER, SKILL_MD, install_skill, remove_skill


class SkillTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("imap_agent_cli.skill.default_skills_dir", return_value=Path(directory)))
        self.enterContext(patch("imap_agent_cli.skill.is_local_development", return_value=True))

    def test_install_skill_creates_and_updates_managed_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = install_skill(root)
            self.assertTrue(first["installed"])
            self.assertTrue(first["updated"])
            skill_path = root / "imap" / "SKILL.md"
            self.assertTrue(skill_path.exists())
            content = skill_path.read_text(encoding="utf-8")
            self.assertIn("name: imap", content)
            self.assertNotIn(MANAGED_MARKER, content)
            self.assertIn("managed-by: imap-agent-cli", content)
            self.assertIn("draft reply", content)

            second = install_skill(root)
            self.assertTrue(second["installed"])
            self.assertFalse(second["updated"])

    def test_skill_text_is_platform_and_agent_neutral(self) -> None:
        self.assertIn("agentic tool", SKILL_MD)
        self.assertIn("uvx imap-agent-cli <command>", SKILL_MD)
        self.assertNotIn("--refresh-package", SKILL_MD)
        self.assertNotIn("Codex", SKILL_MD)
        self.assertNotIn("powershell", SKILL_MD.lower())
        self.assertNotIn("UV_LINK_MODE", SKILL_MD)
        self.assertNotIn("C:\\tmp", SKILL_MD)

    def test_remove_skill_removes_only_managed_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_skill(root)
            removed = remove_skill(root)
            self.assertTrue(removed["removed"])
            self.assertFalse((root / "imap").exists())

    def test_remove_skill_refuses_unmanaged_skill_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "imap"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: imap\n---\ncustom\n", encoding="utf-8")
            with self.assertRaises(AppError):
                remove_skill(root)
            removed = remove_skill(root, force=True)
            self.assertTrue(removed["removed"])

    def test_cli_install_skill_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = main(["install-skill", "--skills-dir", tmp])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["skill"], "imap")
            self.assertTrue((Path(tmp) / "imap" / "SKILL.md").exists())

    def test_cli_remove_skill_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install_skill(Path(tmp))
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = main(["remove-skill", "--skills-dir", tmp])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["removed"])


if __name__ == "__main__":
    unittest.main()
