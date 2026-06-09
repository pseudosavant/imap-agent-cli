from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.config import add_profile, load_config, remove_profile, resolve_profile, set_default_profile


class ConfigTests(unittest.TestCase):
    def test_loads_env_default_profile(self) -> None:
        env = {
            "IMAP_AGENT_CLI_HOST": "imap.example.com",
            "IMAP_AGENT_CLI_PORT": "993",
            "IMAP_AGENT_CLI_USERNAME": "me@example.com",
            "IMAP_AGENT_CLI_PASSWORD": "secret",
            "IMAP_AGENT_CLI_TLS": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(Path(tempfile.gettempdir()) / "missing-imap-agent-cli.toml")
        profile = resolve_profile(config, None)
        self.assertEqual(profile.host, "imap.example.com")
        self.assertEqual(profile.port, 993)
        self.assertEqual(profile.username, "me@example.com")
        self.assertEqual(profile.password, "secret")
        self.assertTrue(profile.tls)

    def test_loads_named_profile_from_config_with_password_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[defaults]
profile = "work"

[profiles.work]
host = "mail.example.com"
port = 993
username = "work@example.com"
password_env = "WORK_IMAP_PASSWORD"
tls = true
""",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"WORK_IMAP_PASSWORD": "secret"}, clear=True):
                config = load_config(path)
        profile = resolve_profile(config, None)
        self.assertEqual(profile.name, "work")
        self.assertEqual(profile.password, "secret")

    def test_add_set_default_and_remove_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            add_profile(
                "work",
                host="mail.example.com",
                port=993,
                username="work@example.com",
                password_env="WORK_IMAP_PASSWORD",
                path=path,
            )
            add_profile(
                "personal",
                host="imap.example.com",
                port=993,
                username="me@example.com",
                password_env="PERSONAL_IMAP_PASSWORD",
                path=path,
            )
            set_default_profile("personal", path=path)
            config = load_config(path)
            self.assertEqual(config.defaults.profile, "personal")
            self.assertIn("work", config.profiles)
            self.assertIn("personal", config.profiles)

            remove_profile("personal", path=path)
            config = load_config(path)
            self.assertNotIn("personal", config.profiles)
            self.assertEqual(config.defaults.profile, "work")


if __name__ == "__main__":
    unittest.main()
