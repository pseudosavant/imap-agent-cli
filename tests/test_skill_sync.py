from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml

from tests import _bootstrap  # noqa: F401

from imap_agent_cli import __version__
from imap_agent_cli.cli import build_parser, main
from imap_agent_cli.errors import AppError
from imap_agent_cli.models import Config
from imap_agent_cli.skill import FORCE_INSTALL_COMMAND, MANAGED_MARKER, install_skill, remove_skill, render_skill, skill_status, sync_skill


def generated(version: str) -> str:
    with patch("imap_agent_cli.skill.__version__", version):
        return render_skill()


def signed(text: str) -> str:
    """Independent signing of fixtures containing a single block-style hash."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    empty = re.sub(r'(?m)^(  managed-content-sha256: )[^\n]*$', r'\1""', normalized)
    digest = hashlib.sha256(empty.encode("utf-8")).hexdigest()
    return empty.replace('managed-content-sha256: ""', f'managed-content-sha256: "sha256:{digest}"', 1)


class SkillSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "imap" / "SKILL.md"
        self.enterContext(patch("imap_agent_cli.skill.default_skills_dir", return_value=self.root))
        self.enterContext(patch("imap_agent_cli.skill.is_local_development", return_value=False))
        self.enterContext(patch("pathlib.Path.home", side_effect=AssertionError("tests must not access real home")))
        self.stderr = self.enterContext(patch("sys.stderr", new=StringIO()))

    def write(self, text: str) -> bytes:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = text.encode("utf-8")
        self.path.write_bytes(content)
        return content

    def older(self) -> str:
        return signed(generated("0.1.5").replace("## Search", "## Earlier Search"))

    def test_metadata_exact_version_hash_and_encoding(self) -> None:
        install_skill()
        content = self.path.read_bytes()
        self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", content)
        self.assertTrue(content.endswith(b"\n"))
        text = content.decode("utf-8")
        front = yaml.safe_load(text.split("---", 2)[1])
        self.assertEqual(front["name"], "imap")
        self.assertIn("agentic tool", front["description"])
        self.assertNotIn("version", front)
        self.assertEqual(front["metadata"]["managed-by"], "imap-agent-cli")
        self.assertEqual(front["metadata"]["managed-version"], __version__)
        self.assertIn(f'managed-version: "{__version__}"', text)
        self.assertRegex(front["metadata"]["managed-content-sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(text, signed(text))
        self.assertEqual(skill_status()["integrity"], "valid")
        self.assertNotIn(MANAGED_MARKER, text)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        with patch("sys.stdout", new=StringIO()) as stdout:
            main(["--version"])
        self.assertEqual(front["metadata"]["managed-version"], stdout.getvalue().strip())
        self.assertIn('managed-version: "v1.02.0RC1+Build.4"', generated("v1.02.0RC1+Build.4"))

    def test_line_endings_verify_without_rewriting(self) -> None:
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=repr(ending)):
                original = self.write(render_skill().replace("\n", ending))
                self.assertEqual(skill_status()["integrity"], "valid")
                self.assertFalse(install_skill()["updated"])
                sync_skill()
                self.assertEqual(self.path.read_bytes(), original)

    def test_hash_covers_body_front_matter_and_formatting(self) -> None:
        for old, new in (
            ("## Earlier Search", "## Custom Search"), ("name: imap", "name: custom"),
            ("description: Use", "description: Always use"),
            ("metadata:\n", "metadata:\n  author: someone\n"),
            ("# IMAP Email Access", "# IMAP Email Access "),
        ):
            with self.subTest(change=new):
                self.write(self.older().replace(old, new))
                self.assertEqual(skill_status()["integrity"], "altered")

    def test_installed_yaml_is_not_reserialized_and_only_hash_value_is_blanked(self) -> None:
        text = self.older().replace("metadata:\n", "metadata: # comment\n  author: 'An agent'\n")
        text = signed(text + '\nExample: managed-content-sha256: ""\n')
        self.write(text)
        self.assertEqual(skill_status()["integrity"], "valid")
        self.write(text.replace('Example: managed-content-sha256: ""', 'Example: managed-content-sha256: "changed"'))
        self.assertEqual(skill_status()["integrity"], "altered")

    def test_bom_and_delimiter_spacing_do_not_hide_metadata(self) -> None:
        original = self.write("\ufeff" + self.older().replace("---\n", "--- \n", 1))
        self.assertEqual(skill_status()["integrity"], "altered")
        sync_skill()
        self.assertEqual(self.path.read_bytes(), original)
        foreign = self.older().replace("managed-by: imap-agent-cli", "managed-by: another-tool")
        original = self.write("\ufeff" + foreign + MANAGED_MARKER)
        self.assertFalse(skill_status()["managed"])
        sync_skill()
        self.assertEqual(self.path.read_bytes(), original)

    def test_absent_skill_is_not_automatically_installed(self) -> None:
        sync_skill()
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir()
        sync_skill()
        self.assertFalse(self.path.exists())
        self.assertFalse(skill_status()["automatic_sync_eligible"])
        self.assertEqual(self.stderr.getvalue(), "")

    def test_unmanaged_and_conflicting_owners_are_untouched_even_with_force(self) -> None:
        for text in (
            "---\nname: imap\n---\ncustom\n",
            self.older().replace("managed-by: imap-agent-cli", "managed-by: another-tool") + MANAGED_MARKER,
            self.older().replace("managed-by: imap-agent-cli", "managed-by: null") + MANAGED_MARKER,
        ):
            with self.subTest(text=text[:70]):
                original = self.write(text)
                sync_skill()
                self.assertFalse(skill_status()["managed"])
                for force in (False, True):
                    with self.assertRaisesRegex(AppError, "unmanaged"):
                        install_skill(force=force)
                self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_pristine_older_skill_updates_automatically_and_explicitly(self) -> None:
        self.write(self.older().replace("\n", "\r\n"))
        self.assertTrue(skill_status()["automatic_sync_eligible"])
        sync_skill()
        self.assertEqual(self.path.read_bytes(), render_skill().encode("utf-8"))
        notice = self.stderr.getvalue()
        for expected in ("0.1.5", __version__, str(self.path)):
            self.assertIn(expected, notice)
        self.assertEqual(len(notice.splitlines()), 1)
        self.write(self.older())
        self.assertTrue(install_skill()["updated"])

    def test_altered_missing_and_malformed_hashes_require_force(self) -> None:
        old = self.older()
        cases = {
            "altered": old + "\nLocal notes.\n",
            "missing": re.sub(r"(?m)^  managed-content-sha256:.*\n", "", old),
            "malformed": re.sub(r"sha256:[0-9a-f]{64}", "sha256:BAD", old),
        }
        for integrity, text in cases.items():
            with self.subTest(integrity=integrity):
                self.stderr.seek(0)
                self.stderr.truncate()
                original = self.write(text)
                status = skill_status()
                self.assertEqual(status["integrity"], integrity)
                self.assertFalse(status["automatic_sync_eligible"])
                self.assertEqual(status["force_install_command"], FORCE_INSTALL_COMMAND)
                sync_skill()
                self.assertEqual(self.path.read_bytes(), original)
                self.assertIn(FORCE_INSTALL_COMMAND, self.stderr.getvalue())
                self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)
                with self.assertRaisesRegex(AppError, re.escape(FORCE_INSTALL_COMMAND)):
                    install_skill()
                self.assertTrue(install_skill(force=True)["updated"])
                self.assertEqual(skill_status()["integrity"], "valid")

    def test_hash_format_is_strict(self) -> None:
        for value in ("", "sha256:" + "A" * 64, "a" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 65):
            with self.subTest(value=value):
                original = self.write(re.sub(r"sha256:[0-9a-f]{64}", value, self.older()))
                self.assertEqual(skill_status()["integrity"], "malformed")
                sync_skill()
                self.assertEqual(self.path.read_bytes(), original)

    def test_equal_version_is_not_rewritten_even_when_altered(self) -> None:
        for suffix in ("", "\nCustom instruction\n"):
            original = self.write(render_skill() + suffix)
            with patch("imap_agent_cli.skill.os.replace") as replace:
                sync_skill()
                replace.assert_not_called()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(skill_status()["version_relation"], "equal")
        self.assertEqual(self.stderr.getvalue(), "")
        with self.assertRaises(AppError):
            install_skill()
        self.assertTrue(install_skill(force=True)["updated"])
        with patch("imap_agent_cli.skill.os.replace") as replace:
            self.assertFalse(install_skill()["updated"])
            self.assertFalse(install_skill(force=True)["updated"])
            replace.assert_not_called()

    def test_newer_version_is_never_downgraded_including_force(self) -> None:
        for suffix in ("", "\nLocal edits\n"):
            original = self.write(generated("9.0") + suffix)
            sync_skill()
            self.assertEqual(skill_status()["version_relation"], "newer")
            for force in (False, True):
                self.assertFalse(install_skill(force=force)["updated"])
                self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_pep440_comparisons(self) -> None:
        for installed, running, relation in (
            ("1.9", "1.10", "older"), ("1.0rc1", "1.0", "older"),
            ("1.0", "1.0.post1", "older"), ("1.0.dev1", "1.0a1", "older"),
            ("1.0+vendor.1", "1.0", "newer"), ("1!0.1", "999.0", "newer"),
            ("1.0.0", "1.0", "equal"),
        ):
            with self.subTest(installed=installed, running=running):
                original = self.write(generated(installed))
                with patch("imap_agent_cli.skill.__version__", running):
                    self.assertEqual(skill_status()["version_relation"], relation)
                    sync_skill()
                    expected = render_skill().encode("utf-8") if relation == "older" else original
                    self.assertEqual(self.path.read_bytes(), expected)

    def test_legacy_is_version_zero_and_migrates(self) -> None:
        self.write(f"---\nname: imap\n---\n{MANAGED_MARKER}\nLegacy instructions\n")
        status = skill_status()
        self.assertTrue(status["managed"])
        self.assertEqual(status["installed_version"], "0")
        self.assertEqual(status["integrity"], "legacy")
        sync_skill()
        self.assertEqual(self.path.read_bytes(), render_skill().encode("utf-8"))
        self.assertIn(f"from 0 to {__version__}", self.stderr.getvalue())

    def test_missing_or_malformed_versions_recover_despite_bad_hash(self) -> None:
        for value in (None, '"not-a-version"', '""', "[1, 2]", "null"):
            with self.subTest(value=value):
                line = "" if value is None else f"  managed-version: {value}\n"
                text = re.sub(r"(?m)^  managed-version:.*\n", line, self.older()) + "Edited body\n"
                self.write(text)
                self.assertTrue(skill_status()["automatic_sync_eligible"])
                sync_skill()
                self.assertEqual(self.path.read_bytes(), render_skill().encode("utf-8"))
                self.write(text)
                self.assertTrue(install_skill()["updated"])

    def test_invalid_running_version_skips_before_reading_skill(self) -> None:
        with patch("imap_agent_cli.skill.__version__", "invalid"), patch("imap_agent_cli.skill._read_skill") as read:
            sync_skill()
            read.assert_not_called()
        self.assertEqual(self.stderr.getvalue(), "")

    def test_local_build_skips_before_home_lookup_but_explicit_install_works(self) -> None:
        original = self.write(self.older())
        with patch("imap_agent_cli.skill.is_local_development", return_value=True):
            with patch("imap_agent_cli.skill.default_skills_dir", side_effect=AssertionError("no home lookup")):
                sync_skill()
            self.assertEqual(self.path.read_bytes(), original)
            status = skill_status()
            self.assertTrue(status["local_development"])
            self.assertEqual(status["automatic_sync_skip_reason"], "local_development")
            self.assertFalse(status["automatic_sync_eligible"])
            self.assertTrue(install_skill()["updated"])
        self.assertEqual(self.stderr.getvalue(), "")

    def test_custom_location_is_not_discovered_automatically(self) -> None:
        custom = self.root / "custom"
        install_skill(custom)
        custom_path = custom / "imap" / "SKILL.md"
        original = self.older().encode("utf-8")
        custom_path.write_bytes(original)
        status = skill_status(custom)
        self.assertEqual(status["location"], "custom")
        self.assertEqual(status["automatic_sync_skip_reason"], "custom_directory")
        self.assertFalse(status["automatic_sync_eligible"])
        sync_skill()
        self.assertFalse(self.path.exists())
        self.assertEqual(custom_path.read_bytes(), original)
        self.assertTrue(install_skill(custom)["updated"])
        self.assertTrue(remove_skill(custom)["removed"])

    def test_status_is_read_only_and_reports_absence(self) -> None:
        status = skill_status()
        self.assertEqual(status["path"], str(self.path))
        self.assertEqual(status["location"], "standard")
        self.assertEqual(status["cli_version"], __version__)
        self.assertFalse(status["installed"])
        self.assertFalse(status["managed"])
        self.assertEqual(status["integrity"], "not_applicable")
        self.assertFalse(self.path.parent.exists())
        original = self.write(self.older())
        skill_status()
        self.assertEqual(self.path.read_bytes(), original)

    def test_atomic_replacement_observes_complete_closed_temp_file(self) -> None:
        original = self.write(self.older())
        real_replace = os.replace

        def observe(source: Path, destination: Path) -> None:
            self.assertEqual(source.parent, destination.parent)
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(source.read_bytes(), render_skill().encode("utf-8"))
            real_replace(source, destination)
            self.assertEqual(destination.read_bytes(), render_skill().encode("utf-8"))

        with patch("imap_agent_cli.skill.os.replace", side_effect=observe) as replace:
            sync_skill()
        replace.assert_called_once()
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_atomic_failures_preserve_original_and_clean_temp(self) -> None:
        for point in ("os.replace", "os.fsync"):
            original = self.write(self.older())
            with patch(f"imap_agent_cli.skill.{point}", side_effect=PermissionError):
                sync_skill()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_concurrent_newer_install_edit_or_removal_aborts_replacement(self) -> None:
        for replacement in (generated("9.0").encode("utf-8"), b"Unmanaged replacement\n", None):
            self.write(self.older())

            def change_during_write(_fd: int) -> None:
                if replacement is None:
                    self.path.unlink()
                else:
                    self.path.write_bytes(replacement)

            with patch("imap_agent_cli.skill.os.fsync", side_effect=change_during_write):
                sync_skill()
            self.assertEqual(self.path.read_bytes() if self.path.exists() else None, replacement)
            self.assertEqual(list(self.path.parent.glob(".SKILL-*")), [])
        self.assertEqual(self.stderr.getvalue(), "")

    def test_concurrent_first_install_preserves_unmanaged_file(self) -> None:
        with patch("imap_agent_cli.skill.os.fsync", side_effect=lambda _fd: self.path.write_bytes(b"someone else")):
            with self.assertRaisesRegex(AppError, "changed during installation"):
                install_skill()
        self.assertEqual(self.path.read_bytes(), b"someone else")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_parse_failure_is_nonfatal_and_does_not_disclose_content(self) -> None:
        for text in (
            "---\nmetadata: [secret invalid yaml\n---\n" + MANAGED_MARKER,
            self.older().replace("metadata:\n", "metadata: {}\nmetadata:\n"),
            "---\nmetadata:\n  <<: {managed-by: another-tool}\n  managed-by: imap-agent-cli\n---\n",
        ):
            original = self.write(text)
            sync_skill()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertNotIn("secret", self.stderr.getvalue())
        self.assertIn("synchronization failed", self.stderr.getvalue())

    def test_removal_preserves_unrelated_files_and_legacy_remains_removable(self) -> None:
        install_skill()
        unrelated = self.path.parent / "notes.txt"
        unrelated.write_text("Keep this", encoding="utf-8")
        self.assertTrue(remove_skill()["removed"])
        self.assertFalse(self.path.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "Keep this")
        self.write(MANAGED_MARKER + "\nOld skill\n")
        self.assertTrue(remove_skill()["removed"])
        self.assertTrue(unrelated.exists())

    def test_unexpected_directories_missing_files_and_links_are_preserved(self) -> None:
        self.path.mkdir(parents=True)
        for operation in (install_skill, remove_skill, skill_status):
            with self.assertRaises(AppError):
                operation()
        self.path.rmdir()
        unrelated = self.path.parent / "notes"
        unrelated.write_text("Keep", encoding="utf-8")
        for operation in (install_skill, remove_skill):
            with self.assertRaises(AppError):
                operation()
        original = self.write(self.older())
        with patch("imap_agent_cli.skill._is_link", return_value=True):
            sync_skill()
            for operation in (install_skill, remove_skill):
                with self.assertRaises(AppError):
                    operation(force=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(unrelated.exists())

    def test_cli_force_status_and_removal_preserve_json_contract(self) -> None:
        original = self.write(self.older() + "\nAltered\n")
        for output_format in ("json", "plain"):
            with patch("sys.stdout", new=StringIO()) as stdout:
                self.assertEqual(main(["skill", "status", "--format", output_format]), 0)
            self.assertIn(FORCE_INSTALL_COMMAND, stdout.getvalue())
            if output_format == "json":
                self.assertEqual(json.loads(stdout.getvalue())["integrity"], "altered")
            self.assertEqual(self.path.read_bytes(), original)
        with patch("sys.stdout", new=StringIO()) as stdout:
            self.assertEqual(main(["skill", "install"]), 1)
            self.assertEqual(stdout.getvalue(), "")
        self.assertIn(FORCE_INSTALL_COMMAND, json.loads(self.stderr.getvalue())["error"]["message"])
        with patch("sys.stdout", new=StringIO()) as stdout:
            self.assertEqual(main(["skill", "install", "--force", "--skills-dir", str(self.root)]), 0)
        self.assertTrue(json.loads(stdout.getvalue())["updated"])
        with patch("sys.stdout", new=StringIO()) as stdout:
            self.assertEqual(main(["skill", "remove"]), 0)
        self.assertTrue(json.loads(stdout.getvalue())["removed"])

    def test_skill_commands_and_their_help_never_sync(self) -> None:
        for argv in (["skill", "install"], ["skill", "remove"], ["skill", "status"], ["install-skill"], ["remove-skill"], ["skill"]):
            for help_flag in ([], ["--help"]):
                with self.subTest(argv=argv + help_flag), patch("imap_agent_cli.cli.sync_skill") as sync, patch("sys.stdout", new=StringIO()):
                    try:
                        main(argv + help_flag)
                    except SystemExit as exc:
                        self.assertIn(exc.code, (0, 2))
                    sync.assert_not_called()

    def test_normal_commands_help_version_about_and_no_args_sync(self) -> None:
        for argv in ([], ["--help"], ["-h"], ["--version"], ["--about"], ["read", "--help"], ["profiles"]):
            self.write(self.older())
            with patch("sys.stdout", new=StringIO()), patch("imap_agent_cli.cli.load_config", return_value=Config()):
                try:
                    self.assertEqual(main(argv), 0)
                except SystemExit as exc:
                    self.assertEqual(exc.code, 0)
            self.assertEqual(self.path.read_bytes(), render_skill().encode("utf-8"))

    def test_json_stdout_stays_valid_with_update_warning_or_altered_notice(self) -> None:
        for mode in ("success", "failure", "altered"):
            self.write(self.older() + ("\nAltered\n" if mode == "altered" else ""))
            self.stderr.seek(0)
            self.stderr.truncate()
            with patch("sys.stdout", new=StringIO()) as stdout, patch("imap_agent_cli.cli.load_config", return_value=Config()):
                if mode == "failure":
                    with patch("imap_agent_cli.skill.os.replace", side_effect=PermissionError):
                        code = main(["profiles"])
                else:
                    code = main(["profiles"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue()), {"profiles": [], "default": "default"})
            self.assertTrue(self.stderr.getvalue())

    def test_sync_failure_preserves_primary_error_and_explicit_error_model(self) -> None:
        self.write(self.older())
        with patch("imap_agent_cli.skill.os.replace", side_effect=PermissionError), patch("sys.stdout", new=StringIO()) as stdout:
            self.assertEqual(main(["read", "--uid", "1"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid_request", self.stderr.getvalue())
        self.stderr.seek(0)
        self.stderr.truncate()
        with patch("imap_agent_cli.skill._read_skill", side_effect=PermissionError), patch("sys.stdout", new=StringIO()) as stdout:
            self.assertEqual(main(["skill", "status"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(self.stderr.getvalue())["error"]["code"], "invalid_request")

    def test_install_and_remove_force_and_status_help(self) -> None:
        parser = build_parser()
        for argv in (["skill", "install"], ["install-skill"], ["skill", "remove"], ["remove-skill"]):
            self.assertTrue(parser.parse_args([*argv, "--force"]).force)
        for argv, expected in ((["skill", "install"], "--force"), (["skill", "status"], "never modifies")):
            with patch("sys.stdout", new=StringIO()) as stdout, self.assertRaises(SystemExit) as result:
                parser.parse_args([*argv, "--help"])
            self.assertEqual(result.exception.code, 0)
            self.assertIn(expected, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
