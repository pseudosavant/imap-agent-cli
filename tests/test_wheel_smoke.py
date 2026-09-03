"""Opt-in installed-distribution checks using an isolated environment and home."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("IMAP_AGENT_CLI_WHEEL_TEST"), "set IMAP_AGENT_CLI_WHEEL_TEST to a built wheel path")
class WheelSmokeTests(unittest.TestCase):
    def test_wheel_sync_and_local_source_exclusions(self) -> None:
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "uv is required for the wheel smoke test")
        wheel = Path(os.environ["IMAP_AGENT_CLI_WHEEL_TEST"]).resolve()
        self.assertTrue(wheel.is_file())
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment"
            fake_home = root / "home"
            fake_home.mkdir()
            env = {**os.environ, "HOME": str(fake_home), "USERPROFILE": str(fake_home)}
            env.pop("PYTHONPATH", None)
            env.pop("VIRTUAL_ENV", None)
            if os.name == "nt":
                env["HOMEDRIVE"] = fake_home.drive
                env["HOMEPATH"] = str(fake_home)[len(fake_home.drive):]

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(
                    list(args), cwd=root, env=env, capture_output=True, text=True,
                    encoding="utf-8", timeout=60, check=False,
                )
                self.assertEqual(result.returncode, 0, f"command failed: {args}\n{result.stdout}\n{result.stderr}")
                return result

            run(uv, "venv", str(environment), "--python", sys.executable)
            python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            run(uv, "pip", "install", "--python", python, str(wheel))

            def cli(*args: str) -> subprocess.CompletedProcess[str]:
                return run(python, "-m", "imap_agent_cli.cli", *args)

            version = cli("--version").stdout.strip()
            installed_version = run(python, "-c", "from importlib.metadata import version\nprint(version('imap-agent-cli'))").stdout.strip()
            self.assertEqual(version, installed_version)
            self.assertEqual(cli("--version").stderr, "")
            path = fake_home / ".agents" / "skills" / "imap" / "SKILL.md"
            self.assertFalse(path.exists())
            cli("skill", "install")
            canonical = path.read_bytes()
            rendered = run(python, "-c", "from imap_agent_cli.skill import render_skill\nimport sys\nsys.stdout.write(render_skill())").stdout
            self.assertEqual(canonical, rendered.encode("utf-8"))
            status = json.loads(cli("skill", "status").stdout)
            self.assertFalse(status["local_development"])
            self.assertEqual(status["installed_version"], version)
            self.assertEqual(status["integrity"], "valid")

            def older() -> bytes:
                text = re.sub(r'(?m)^  managed-version:.*$', '  managed-version: "0.0.0"', rendered)
                empty = re.sub(r'(?m)^  managed-content-sha256:.*$', '  managed-content-sha256: ""', text)
                digest = hashlib.sha256(empty.encode("utf-8")).hexdigest()
                return empty.replace('managed-content-sha256: ""', f'managed-content-sha256: "sha256:{digest}"').encode("utf-8")

            path.write_bytes(older())
            status = json.loads(cli("skill", "status").stdout)
            self.assertTrue(status["automatic_sync_eligible"])
            self.assertEqual(path.read_bytes(), older())
            updated = cli("--version")
            self.assertEqual(updated.stdout.strip(), version)
            self.assertIn(f"from 0.0.0 to {version}", updated.stderr)
            self.assertEqual(path.read_bytes(), canonical)

            uvx_skills = root / "uvx-skills"
            result = run(uv, "tool", "run", "--from", str(repository), "imap-agent-cli", "skill", "install", "--skills-dir", str(uvx_skills))
            self.assertTrue(json.loads(result.stdout)["installed"])
            self.assertEqual((uvx_skills / "imap" / "SKILL.md").read_bytes(), canonical)

            # These installations produce real PEP 610 directory metadata.
            # Explicit skill installation must still work in both cases.
            for source_args in ((str(repository),), ("--editable", str(repository))):
                run(uv, "pip", "install", "--python", python, "--reinstall-package", "imap-agent-cli", *source_args)
                path.write_bytes(older())
                skipped = cli("--version")
                self.assertEqual(skipped.stderr, "")
                self.assertEqual(path.read_bytes(), older())
                status = json.loads(cli("skill", "status").stdout)
                self.assertTrue(status["local_development"])
                self.assertFalse(status["automatic_sync_eligible"])
                self.assertEqual(status["automatic_sync_skip_reason"], "local_development")
                result = json.loads(cli("skill", "install").stdout)
                self.assertTrue(result["updated"])
                self.assertEqual(path.read_bytes(), canonical)
