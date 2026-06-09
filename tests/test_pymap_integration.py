from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
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


def _run_cli(args: list[str], env: dict[str, str]) -> dict[str, object]:
    env = {**env, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "imap_agent_cli.cli", *args],
        cwd=ROOT / "src",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


@unittest.skipUnless(os.environ.get("IMAP_AGENT_CLI_TEST_PYMAP") == "1", "set IMAP_AGENT_CLI_TEST_PYMAP=1")
class PymapIntegrationTests(unittest.TestCase):
    def test_folders_search_read_and_draft_with_pymap_demo_data(self) -> None:
        if not shutil.which("pymap"):
            self.skipTest("pymap executable not found")
        port = _free_port()
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
                **os.environ,
                "IMAP_AGENT_CLI_HOST": "127.0.0.1",
                "IMAP_AGENT_CLI_PORT": str(port),
                "IMAP_AGENT_CLI_USERNAME": "demouser",
                "IMAP_AGENT_CLI_PASSWORD": "demopass",
                "IMAP_AGENT_CLI_TLS": "false",
            }
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
