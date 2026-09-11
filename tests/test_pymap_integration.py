from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import _bootstrap  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "imap_agent_cli.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("pymap process exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for pymap on port {port}")


def _run_cli(args: list[str], env: dict[str, str], *, input_text: str | None = None, expected_code: int = 0) -> dict[str, object]:
    env = {**env, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "imap_agent_cli.cli", *args],
        cwd=ROOT / "src",
        env=env,
        text=True,
        capture_output=True,
        input=input_text,
        timeout=30,
        check=False,
    )
    if result.returncode != expected_code:
        raise AssertionError(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


@unittest.skipUnless(os.environ.get("IMAP_AGENT_CLI_TEST_PYMAP") == "1", "set IMAP_AGENT_CLI_TEST_PYMAP=1")
class PymapIntegrationTests(unittest.TestCase):
    def test_folders_search_read_and_draft_with_pymap_demo_data(self) -> None:
        if not shutil.which("pymap"):
            self.skipTest("pymap executable not found")
        port = _free_port()
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests" / "pymap_server_runner.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-tls",
                "dict",
                "--demo-data",
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_port(port, process)
            env = {
                **{key: value for key, value in os.environ.items() if not key.startswith("IMAP_AGENT_CLI_")},
                "HOME": str(home), "USERPROFILE": str(home),
                "IMAP_AGENT_CLI_CONFIG": str(home / "config.toml"),
            }
            from imapclient import IMAPClient

            def flags():
                with IMAPClient("127.0.0.1", port=port, ssl=False, timeout=10) as client:
                    client.login("demouser", "demopass")
                    client.select_folder("INBOX", readonly=True)
                    return client.get_flags(client.search(["ALL"]))

            initial_flags = flags()
            setup = _run_cli(["setup", "--host", "127.0.0.1", "--port", str(port),
                              "--username", "demouser", "--sender", "demo@example.com", "--no-tls",
                              "--ssl-mode", "disabled", "--password-stdin", "--non-interactive"],
                             env, input_text="demopass\n")
            self.assertTrue(setup["ready"])
            self.assertTrue(setup["credential_saved"])
            self.assertTrue((home / ".agents" / "skills" / "imap" / "SKILL.md").exists())
            config_path, secret_path = home / "config.toml", home / "credentials.toml"
            original = config_path.read_bytes(), secret_path.read_bytes()
            repeated = _run_cli(["setup", "--non-interactive"], env)
            self.assertTrue(repeated["ready"])
            self.assertEqual(original, (config_path.read_bytes(), secret_path.read_bytes()))
            check = _run_cli(["config", "check"], env)
            self.assertTrue(check["ok"])
            failed = _run_cli(["setup", "--replace-password", "--password-stdin", "--non-interactive"],
                              env, input_text="incorrect-dummy\n", expected_code=1)
            self.assertFalse(failed["ready"])
            self.assertEqual(original, (config_path.read_bytes(), secret_path.read_bytes()))
            folders = _run_cli(["folders"], env)
            folder_names = {folder["name"] for folder in folders["folders"]}  # type: ignore[index]
            self.assertIn("INBOX", folder_names)
            inbox = next(folder["name"] for folder in folders["folders"] if folder["name"] == "INBOX")  # type: ignore[index]

            search = _run_cli(["search", "--folder", str(inbox), "--max-results", "1"], env)
            results = search["results"]  # type: ignore[index]
            self.assertTrue(results)
            first = results[0]
            uid = int(first["uid"])  # type: ignore[index]

            message = _run_cli(["read", "--folder", str(inbox), "--uid", str(uid), "--body-format", "plain"], env)
            self.assertEqual(message["folder"], inbox)
            self.assertEqual(message["uid"], uid)
            self.assertEqual(flags(), initial_flags)

            draft = _run_cli(
                [
                    "draft",
                    "create",
                    "--drafts-folder",
                    "INBOX",
                    "--to",
                    "recipient@example.com",
                    "--subject",
                    "pymap integration draft",
                    "--body",
                    "This is only a local integration-test draft.",
                ],
                env,
            )
            self.assertTrue(draft["created"])
            self.assertEqual(draft["drafts_folder"], "INBOX")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
