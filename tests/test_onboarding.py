from __future__ import annotations

import imaplib
import json
import os
import ssl
import tempfile
import unittest
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import _bootstrap  # noqa: F401
from imap_agent_cli.cli import main
from imap_agent_cli.config import add_profile, load_config, resolve_profile
from imap_agent_cli.credentials import save_credential
from imap_agent_cli.errors import AppError
from imap_agent_cli.imap_client import ImapSession
from imap_agent_cli.models import Defaults, Profile
from imap_agent_cli.onboarding import read_masked_password
from imap_agent_cli.providers import parse_target
from imap_agent_cli.storage import paths
from imap_agent_cli.storage import read_document, write_document
from imap_agent_cli.verification import profile_metadata, verify_profile


SECRET = "dummy-onboarding-password"


class Terminal(StringIO):
    def isatty(self):
        return True


def verified(profile, defaults):
    return {"ok": True, "profile": profile_metadata(profile), "checks": [],
            "verification": "verified", "draft_creation": "not_tested"}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.root / "config.toml"
        self.credentials = self.root / "credentials.toml"
        self.enterContext(patch("imap_agent_cli.config.CONFIG_PATH", self.config))
        self.enterContext(paths(str(self.config)))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch("imap_agent_cli.skill.default_skills_dir", return_value=self.root / "skills"))
        self.enterContext(patch("imap_agent_cli.skill.is_local_development", return_value=True))
        self.stdout = self.enterContext(patch("sys.stdout", new=StringIO()))
        self.stderr = self.enterContext(patch("sys.stderr", new=Terminal()))
        self.stdin = self.enterContext(patch("sys.stdin", new=Terminal()))
        self.verify = self.enterContext(patch("imap_agent_cli.onboarding.verify_profile", side_effect=verified))
        self.mask = self.enterContext(patch("imap_agent_cli.onboarding.read_masked_password", return_value=SECRET))

    def run_setup(self, *args):
        self.stdout.seek(0)
        self.stdout.truncate()
        self.stderr.seek(0)
        self.stderr.truncate()
        code = main(["setup", *args])
        self.assertNotIn(SECRET, self.stdout.getvalue() + self.stderr.getvalue())
        return code

    def test_clean_setup_installs_skill_and_fresh_process_resolution_needs_no_env(self):
        self.assertEqual(self.run_setup("alex@gmail.com"), 0)
        report = json.loads(self.stdout.getvalue())
        self.assertTrue(report["ready"])
        self.assertTrue(report["skill"]["installed"])
        self.assertNotIn(SECRET, self.config.read_text())
        profile = resolve_profile(load_config(), None)
        self.assertEqual(profile.password, SECRET)
        self.assertEqual(profile.credential_source, "file")
        self.assertEqual(profile.credentials_file, "")
        self.assertNotIn(SECRET, repr(profile))
        self.assertTrue((self.root / "skills" / "imap" / "SKILL.md").is_file())

    def test_repeat_setup_reuses_secret_and_preserves_bytes(self):
        self.run_setup("alex@gmail.com")
        before = {path: path.read_bytes() for path in (self.config, self.credentials, self.root / "skills" / "imap" / "SKILL.md")}
        self.mask.reset_mock()
        self.assertEqual(self.run_setup(), 0)
        self.mask.assert_not_called()
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_unknown_domain_asks_only_for_host(self):
        self.stdin.write("imap.example.com\n")
        self.stdin.seek(0)
        self.assertEqual(self.run_setup("alex@example.com"), 0)
        self.assertNotIn("IMAP username", self.stderr.getvalue())

    def test_setup_completes_partial_existing_profile(self):
        self.config.write_text('[profiles.default]\nusername = "alex@example.com"\n# Keep this note\n')
        self.assertEqual(self.run_setup("--host", "imap.example.com"), 0)
        self.assertEqual(resolve_profile(load_config(), None).username, "alex@example.com")
        self.assertIn("# Keep this note", self.config.read_text())

    def test_multiple_profiles_preserve_default_and_unrelated_comments(self):
        self.run_setup("alex@gmail.com")
        with self.config.open("a") as stream:
            stream.write('\n# Keep this note\n[unrelated]\nvalue = "keep"\n')
        self.assertEqual(self.run_setup("alex@fastmail.com", "--profile", "work"), 0)
        self.assertEqual(load_config().defaults.profile, "default")
        self.assertIn("# Keep this note", self.config.read_text())
        self.assertIn('value = "keep"', self.config.read_text())
        self.assertEqual(resolve_profile(load_config(), "default").username, "alex@gmail.com")
        self.assertEqual(self.run_setup("--profile", "work", "--set-default"), 0)
        self.assertEqual(load_config().defaults.profile, "work")

    def test_failed_auth_does_not_save_account_or_secret(self):
        self.verify.side_effect = lambda p, d: {**verified(p, d), "ok": False, "verification": "failed", "checks": [{"name": "auth_failed", "ok": False, "error": "Login rejected."}]}
        self.assertEqual(self.run_setup("alex@gmail.com"), 1)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.credentials.exists())
        self.assertFalse((self.root / "skills").exists())

    def test_failed_replacement_keeps_working_account_and_secret(self):
        self.run_setup("alex@gmail.com")
        before = self.config.read_bytes(), self.credentials.read_bytes()
        self.mask.return_value = "replacement-dummy"
        self.verify.side_effect = lambda p, d: {**verified(p, d), "ok": False, "checks": []}
        self.assertEqual(self.run_setup("--replace-password"), 1)
        self.assertEqual(before, (self.config.read_bytes(), self.credentials.read_bytes()))

    def test_successful_replacement_uses_new_secret(self):
        self.run_setup("alex@gmail.com")
        self.mask.return_value = "replacement-dummy"
        self.assertEqual(self.run_setup("--replace-password"), 0)
        self.assertEqual(resolve_profile(load_config(), None).password, "replacement-dummy")
        self.assertNotIn(SECRET, self.credentials.read_text())

    def test_switching_credentials_file_preserves_original(self):
        self.run_setup("alex@gmail.com")
        original = self.credentials.read_bytes()
        alternate = self.root / "new-secrets.toml"
        self.assertEqual(self.run_setup("--replace-password", "--credentials-file", str(alternate)), 0)
        self.assertEqual(original, self.credentials.read_bytes())
        self.assertTrue(alternate.exists())
        self.assertEqual(resolve_profile(load_config(), None).password, SECRET)

    def test_concurrent_edit_is_preserved(self):
        self.run_setup("alex@gmail.com")
        document, previous = read_document(self.config)
        changed = self.config.read_bytes() + b"\n# Concurrent change\n"
        self.config.write_bytes(changed)
        document["profiles"]["default"]["username"] = "someone@example.com"
        with self.assertRaises(AppError):
            write_document(self.config, document, previous)
        self.assertEqual(self.config.read_bytes(), changed)

    def test_password_variable_name_is_validated_before_network(self):
        self.assertEqual(self.run_setup("alex@gmail.com", "--password-env", "BAD NAME"), 1)
        self.verify.assert_not_called()
        self.mask.assert_not_called()

    def test_password_and_json_cannot_share_stdin(self):
        self.stdin.write(SECRET + "\n")
        self.stdin.seek(0)
        self.assertEqual(main(["read", "--json", "-", "--password-stdin"]), 1)
        self.assertEqual(self.stdin.tell(), 0)
        self.assertNotIn(SECRET, self.stderr.getvalue())

    def test_cancelled_prompt_creates_no_files(self):
        self.mask.side_effect = KeyboardInterrupt
        self.assertEqual(self.run_setup("alex@gmail.com"), 130)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.credentials.exists())

    def test_noninteractive_missing_input_never_prompts_or_writes(self):
        self.assertEqual(self.run_setup("alex@gmail.com", "--non-interactive"), 1)
        self.mask.assert_not_called()
        self.verify.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_noninteractive_env_is_not_saved(self):
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_PASSWORD": SECRET}):
            self.assertEqual(self.run_setup("alex@gmail.com", "--non-interactive"), 0)
        self.mask.assert_not_called()
        self.assertFalse(self.credentials.exists())
        self.assertFalse(resolve_profile(load_config(), None).password)

    def test_stdin_is_explicit_and_saved_without_prompt(self):
        self.stdin.write(SECRET + "\n")
        self.stdin.seek(0)
        self.assertEqual(self.run_setup("alex@gmail.com", "--non-interactive", "--password-stdin", "--no-skill"), 0)
        self.mask.assert_not_called()
        self.assertEqual(resolve_profile(load_config(), None).password, SECRET)

    def test_env_override_replacement_refused(self):
        self.run_setup("alex@gmail.com")
        self.mask.reset_mock()
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_PASSWORD": SECRET}):
            self.assertEqual(self.run_setup("--replace-password"), 1)
        self.mask.assert_not_called()
        self.assertIn("Remove-Item Env:IMAP_AGENT_CLI_PASSWORD", self.stderr.getvalue())

    def test_endpoint_change_cannot_reuse_saved_credential(self):
        self.run_setup("alex@gmail.com")
        self.verify.reset_mock()
        self.assertEqual(self.run_setup("--host", "other.example.com"), 1)
        self.verify.assert_not_called()
        with self.assertRaises(AppError):
            resolve_profile(load_config(), None, host="other.example.com")

    def test_malformed_credentials_bypass_and_redaction(self):
        self.run_setup("alex@gmail.com")
        self.credentials.write_text('password = "' + SECRET)
        with self.assertRaises(AppError) as caught:
            resolve_profile(load_config(), None)
        self.assertNotIn(SECRET, str(caught.exception))
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_PASSWORD": "environment-dummy"}):
            self.assertEqual(resolve_profile(load_config(), None).password, "environment-dummy")
        self.assertEqual(resolve_profile(load_config(), None, password="stdin-dummy").password, "stdin-dummy")

    def test_non_secret_environment_overrides_saved_config(self):
        self.run_setup("alex@gmail.com")
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_HOST": "override.example.com", "IMAP_AGENT_CLI_PASSWORD": SECRET}):
            profile = resolve_profile(load_config(), None)
            self.assertEqual(profile.host, "override.example.com")
            self.assertEqual(resolve_profile(load_config(), None, host="flag.example.com").host, "flag.example.com")

    def test_profile_password_env_then_global_then_file(self):
        self.run_setup("alex@gmail.com", "--password-env", "WORK_PASSWORD")
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_PASSWORD": "global-dummy", "WORK_PASSWORD": "named-dummy"}):
            self.assertEqual(resolve_profile(load_config(), None).password, "named-dummy")
        with patch.dict(os.environ, {"IMAP_AGENT_CLI_PASSWORD": "global-dummy"}):
            self.assertEqual(resolve_profile(load_config(), None).password, "global-dummy")
        self.assertEqual(resolve_profile(load_config(), None).password, SECRET)

    def test_credential_write_failure_preserves_files(self):
        self.run_setup("alex@gmail.com")
        before = self.config.read_bytes(), self.credentials.read_bytes()
        with patch("imap_agent_cli.storage.os.replace", side_effect=PermissionError):
            self.assertEqual(self.run_setup("--replace-password"), 1)
        self.assertEqual(before, (self.config.read_bytes(), self.credentials.read_bytes()))
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_config_write_failure_cannot_switch_existing_secret(self):
        self.run_setup("alex@gmail.com")
        old = self.config.read_bytes()
        self.mask.return_value = "replacement-dummy"
        with patch("imap_agent_cli.onboarding.save_profile", side_effect=AppError("config_invalid", "Write failed.")):
            self.assertEqual(self.run_setup("--replace-password"), 1)
        self.assertEqual(self.config.read_bytes(), old)
        self.assertEqual(resolve_profile(load_config(), None).password, SECRET)
        self.assertFalse(json.loads(self.stdout.getvalue())["ready"])

    def test_unmanaged_skill_preserved_and_partial_completion_reported(self):
        target = self.root / "skills" / "imap" / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("User instructions")
        self.assertEqual(self.run_setup("alex@gmail.com"), 1)
        self.assertTrue(json.loads(self.stdout.getvalue())["account_saved"])
        self.assertEqual(target.read_text(), "User instructions")
        self.assertEqual(self.run_setup("--format", "plain"), 1)
        self.assertIn("Setup incomplete.", self.stdout.getvalue())
        self.assertNotIn("Check passed.", self.stdout.getvalue())

    def test_local_check_never_connects_or_syncs_or_writes(self):
        self.run_setup("alex@gmail.com")
        before = self.config.read_bytes(), self.credentials.read_bytes()
        with patch("imap_agent_cli.cli.ImapSession") as session, patch("imap_agent_cli.cli.sync_skill") as sync:
            self.assertEqual(main(["config", "check", "--local"]), 0)
            session.assert_not_called()
            sync.assert_not_called()
        self.assertEqual(before, (self.config.read_bytes(), self.credentials.read_bytes()))

    def test_custom_paths_work_on_both_sides_of_subcommand(self):
        alt = self.root / "alternate.toml"
        secret = self.root / "alternate-secrets.toml"
        self.assertEqual(main(["--config", str(alt), "setup", "alex@gmail.com", "--credentials-file", str(secret)]), 0)
        self.assertTrue(secret.exists())
        self.assertFalse(self.config.exists())
        self.assertEqual(resolve_profile(load_config(alt), None).password, SECRET)

    def test_legacy_add_profile_keeps_unknown_tables_and_comments(self):
        self.config.write_text('# Note\n[custom]\nvalue = "keep"\n')
        add_profile("work.one", host="imap.example.com", username="alex@example.com", password_env="WORK_PASSWORD")
        self.assertIn("# Note", self.config.read_text())
        self.assertIn("[custom]", self.config.read_text())
        self.assertIn("work.one", load_config().profiles)

    def test_microsoft_rejected_before_credentials_or_network(self):
        for target in ("alex@outlook.com", "https://outlook.office.com/", "outlook.office365.com"):
            self.assertEqual(self.run_setup(target), 1)
        self.mask.assert_not_called()
        self.verify.assert_not_called()
        self.assertFalse(self.config.exists())


class VerificationTests(unittest.TestCase):
    def fixture(self):
        server = MagicMock()
        server.list_folders.return_value = [([], "/", "INBOX"), ([b"\\Drafts"], "/", "Drafts")]
        server.select_folder.return_value = {b"UIDNEXT": 1001}
        server.search.return_value = [999, 1000]
        server.fetch.return_value = {1000: {b"ENVELOPE": object()}}
        profile = Profile(name="default", host="imap.example.com", username="alex@example.com", password=SECRET)
        session = ImapSession(profile, Defaults())
        session.server = server
        session.security = {"encrypted": True}
        factory = MagicMock()
        factory.return_value.__enter__.return_value = session
        return profile, server, factory

    def test_verification_is_bounded_metadata_only_and_readonly(self):
        profile, server, factory = self.fixture()
        report = verify_profile(profile, Defaults(), session_factory=factory)
        self.assertTrue(report["ok"])
        server.select_folder.assert_called_once_with("INBOX", readonly=True)
        server.search.assert_called_once_with(["UID", "751:1000"])
        self.assertEqual(server.fetch.call_args.args[0], [1000])
        self.assertFalse(any("PEEK" in field or "BODY[" in field or field == "RFC822" for field in server.fetch.call_args.args[1]))
        server.folder_status.assert_not_called()
        server.append.assert_not_called()

    def test_empty_mailbox_and_missing_drafts_do_not_fail_reading(self):
        profile, server, factory = self.fixture()
        server.select_folder.return_value = {b"UIDNEXT": 1}
        server.list_folders.return_value = [([], "/", "INBOX")]
        report = verify_profile(profile, Defaults(), session_factory=factory)
        self.assertTrue(report["ok"])
        server.fetch.assert_not_called()
        server.search.assert_not_called()
        self.assertEqual(report["checks"][-1]["required"], False)

    def test_configured_nonexistent_drafts_is_not_claimed_verified(self):
        profile, server, factory = self.fixture()
        report = verify_profile(replace(profile, drafts_folder="Missing"), Defaults(), session_factory=factory)
        self.assertFalse(report["checks"][-1]["ok"])
        self.assertTrue(report["ok"])

    def test_select_failure_has_no_readwrite_fallback(self):
        profile, server, factory = self.fixture()
        server.select_folder.side_effect = OSError(SECRET)
        report = verify_profile(profile, Defaults(), session_factory=factory)
        self.assertFalse(report["ok"])
        server.select_folder.assert_called_once_with("INBOX", readonly=True)
        self.assertNotIn(SECRET, json.dumps(report))

    def test_login_rejection_does_not_retry_or_echo_server_error(self):
        profile, server, _ = self.fixture()
        server.login.side_effect = imaplib.IMAP4.error(SECRET)
        with patch("imapclient.IMAPClient", return_value=server) as client:
            with self.assertRaises(AppError) as caught:
                with ImapSession(replace(profile, ssl_mode="preferred"), Defaults()):
                    pass
        self.assertEqual(caught.exception.code, "auth_failed")
        self.assertNotIn(SECRET, str(caught.exception))
        client.assert_called_once()
        server.login.assert_called_once()

    def test_certificate_failure_never_retries_or_sends_password(self):
        profile, server, _ = self.fixture()
        with patch("imapclient.IMAPClient", side_effect=ssl.SSLCertVerificationError(SECRET)) as client:
            with self.assertRaises(AppError) as caught:
                with ImapSession(replace(profile, ssl_mode="preferred"), Defaults()):
                    pass
        self.assertEqual(caught.exception.code, "tls_failed")
        self.assertNotIn(SECRET, str(caught.exception))
        client.assert_called_once()
        server.login.assert_not_called()

    def test_failed_starttls_never_sends_password(self):
        for mode in ("required", "preferred"):
            profile, server, _ = self.fixture()
            server.starttls.side_effect = imaplib.IMAP4.error(SECRET)
            with patch("imapclient.IMAPClient", return_value=server):
                with self.assertRaises(AppError):
                    with ImapSession(replace(profile, tls=False, ssl_mode=mode), Defaults()):
                        pass
            server.login.assert_not_called()
            server.logout.assert_called_once()

    def test_login_connection_abort_is_not_reported_as_bad_password(self):
        profile, server, _ = self.fixture()
        server.login.side_effect = imaplib.IMAP4.abort(SECRET)
        with patch("imapclient.IMAPClient", return_value=server):
            with self.assertRaises(AppError) as caught:
                with ImapSession(profile, Defaults()):
                    pass
        self.assertEqual(caught.exception.code, "connection_failed")
        self.assertNotIn(SECRET, str(caught.exception))


class MaskedInputTests(unittest.TestCase):
    def test_redirected_input_never_falls_back_to_echo(self):
        with self.assertRaises(AppError):
            read_masked_password(StringIO(SECRET), Terminal())

    def test_actual_prompt_masks_paste_and_backspace(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output.vt100 import Vt100_Output
        from prompt_toolkit.data_structures import Size
        output = Terminal()
        with create_pipe_input() as pipe:
            pipe.send_text("dummy\x7fX\r")
            with patch("prompt_toolkit.input.create_input", return_value=pipe), patch("prompt_toolkit.output.create_output", return_value=Vt100_Output(output, lambda: Size(rows=24, columns=80), enable_cpr=False)):
                self.assertEqual(read_masked_password(Terminal(), output), "dummX")
        self.assertIn("*", output.getvalue())
        self.assertNotIn("dummy", output.getvalue())
        self.assertNotIn("dummX", output.getvalue())


class ProviderTests(unittest.TestCase):
    def test_targets_are_hints_not_http_discovery(self):
        self.assertEqual(parse_target("https://app.fastmail.com/")["host"], "imap.fastmail.com")
        self.assertEqual(parse_target("alex@example.com"), {"username": "alex@example.com", "sender": "alex@example.com"})
        self.assertEqual(parse_target("imap://localhost:143")["ssl_mode"], "required")
        for target in ("imaps://user:password@example.com", "https://evil.example/", "https://app.fastmail.com/?token=secret", "imaps://example.com:99999"):
            with self.assertRaises(AppError):
                parse_target(target)
