"""Interactive account setup. Ordinary mailbox commands never prompt."""
from __future__ import annotations

import os
import re
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from .config import _environment, config_path, load_config, save_profile, validate_profile
from .credentials import credentials_path, resolve_credential, save_credential, remove_credential
from .errors import AppError
from .models import Profile
from .providers import PROVIDERS, parse_target, provider_for, reject_microsoft
from .render import write_json
from .skill import install_skill, skill_status
from .storage import CREDENTIALS_OVERRIDE, read_bytes
from .verification import verify_profile


def read_masked_password(stdin, stderr) -> str:
    if not stdin.isatty() or not stderr.isatty():
        raise AppError("terminal_required", "Masked input requires a terminal. Run uvx imap-agent-cli setup in your terminal or supply --password-stdin from a trusted secret source.")
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import DummyHistory
    from prompt_toolkit.input import create_input
    from prompt_toolkit.output import create_output
    try:
        with closing(create_input(stdin=stdin)) as terminal_input:
            session = PromptSession(input=terminal_input, output=create_output(stdout=stderr),
                                    history=DummyHistory(), is_password=True, enable_suspend=False)
            return session.prompt("Password: ")
    except (EOFError, OSError):
        raise AppError("terminal_required", "Masked input is unavailable. Run setup in an interactive terminal. No password was saved.") from None


def inspect_skill(directory: str | None) -> dict:
    try:
        return skill_status(Path(directory).expanduser() if directory else None)
    except (AppError, OSError, UnicodeError):
        return {"installed": False, "error": "Cannot inspect the skill. Run uvx imap-agent-cli skill status with the same skills directory."}


def write_report(report: dict, output_format: str) -> int:
    issues = [item["error"] for item in report.get("checks", []) if item.get("error")]
    report["next_steps"] = list(dict.fromkeys([*report.get("next_steps", []), *issues]))
    for issue in report["next_steps"]:
        print(issue, file=sys.stderr)
    if output_format == "json":
        write_json(report)
    else:
        if "ready" in report:
            print("Setup complete." if report["ready"] else "Setup incomplete.")
        else:
            print("Check passed." if report.get("ok") else "Check incomplete.")
        print()
        profile = report.get("profile", {})
        print(f"Profile: {profile.get('name', 'unresolved')}")
        print(f"Credential source: {profile.get('credential_source', 'unavailable')}")
        print(f"Mailbox verification: {report.get('verification', 'not_checked')}")
        print(f"Draft creation: {report.get('draft_creation', 'not_tested')}")
        for key, label in (("config_path", "Configuration"), ("credentials_path", "Credentials")):
            if report.get(key):
                print(f"\n{label}:\n  {report[key]}")
        skill = report.get("skill", {})
        if skill.get("path"):
            print(f"\nAgent skill:\n  {skill['path']}")
        if report.get("ready") and skill.get("installed"):
            print("\nStart a new agent session if the skill is not available.\nAsk your agent:\n\n  Use $imap to list the five newest messages in INBOX.\n  Show senders and subjects only.")
    return 0 if report.get("ready", report.get("ok", False)) else 1


def setup(args) -> int:
    path = config_path()
    expected = read_bytes(path)
    config = load_config(path)
    name = args.profile or config.defaults.profile
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise AppError("invalid_request", "Profile names must start with a letter or number and contain only letters, numbers, dots, underscores, or hyphens.")
    interactive = not args.non_interactive and sys.stdin.isatty() and sys.stderr.isatty()

    def ask(prompt: str) -> str:
        if not interactive:
            raise AppError("setup_incomplete", f"{prompt.rstrip(': ')} is required. Run uvx imap-agent-cli setup --profile {name} in your terminal, or supply the missing setting with --non-interactive.")
        print(f"\n{prompt}", end=" ", file=sys.stderr, flush=True)
        value = sys.stdin.readline().strip()
        if not value:
            raise AppError("interrupted", "Setup cancelled. No account was saved.")
        return value

    profile = config.profiles.get(name) or _environment(Profile(name=name, host=""))
    if not profile.password:
        variable = profile.password_env
        password = os.environ.get(variable) or os.environ.get("IMAP_AGENT_CLI_PASSWORD")
        if password:
            profile = replace(profile, password=password, credential_source="environment:" + (variable if os.environ.get(variable) else "IMAP_AGENT_CLI_PASSWORD"))
    target = args.target
    if not target and not profile.host and not args.host:
        target = ask("Email address, IMAP hostname, or webmail URL:")
    changes = parse_target(target) if target else {}
    if target and name in config.profiles and changes.get("username", profile.username) != profile.username and not args.profile:
        raise AppError("invalid_request", "This is a different account. Run setup --profile work to add it without replacing your default account.")
    for key in ("host", "port", "username", "tls", "ssl_mode", "drafts_folder", "password_env", "sender"):
        value = getattr(args, key, None)
        if value is not None:
            if key in changes and key not in {"sender"} and changes[key] != value:
                raise AppError("invalid_request", "The supplied target conflicts with connection flags. Supply matching values or omit the target.")
            changes[key] = value
    profile = replace(profile, **changes)
    if not profile.host:
        host = ask("IMAP hostname (from your email provider):")
        profile = replace(profile, **parse_target(host))
    reject_microsoft(profile.host)
    if not profile.username:
        profile = replace(profile, username=ask("IMAP username (usually your email address):"))
    if not profile.sender and "@" in profile.username:
        profile = replace(profile, sender=profile.username)
    if args.password_env:
        password = os.environ.get(args.password_env) or os.environ.get("IMAP_AGENT_CLI_PASSWORD")
        source = "environment:" + (args.password_env if os.environ.get(args.password_env) else "IMAP_AGENT_CLI_PASSWORD")
        profile = replace(profile, password=password, credential_source=source if password else "missing")
    defaults = config.defaults
    if args.default_folder:
        defaults = replace(defaults, default_folder=args.default_folder)
    validate_profile(profile)
    if args.replace_password and profile.credential_source.startswith("environment:"):
        variable = profile.credential_source.partition(":")[2]
        raise AppError("credential_override", f"Unset {variable} before replacing a saved password. PowerShell: Remove-Item Env:{variable}. Bash: unset {variable}. Then run uvx imap-agent-cli setup --profile {name} --replace-password.")
    if args.replace_password and not interactive and not args.password_stdin:
        raise AppError("terminal_required", "Password replacement needs a terminal or explicit --password-stdin. Run uvx imap-agent-cli setup --replace-password in your terminal.")
    entered = None
    if args.password_stdin:
        entered = sys.stdin.readline().rstrip("\r\n")
        profile = replace(profile, password=entered, credential_source="stdin")
    elif not args.replace_password:
        profile = resolve_credential(profile, path)
    credential_path = credentials_path(path, profile)
    if CREDENTIALS_OVERRIDE.get() is not None:
        profile = replace(profile, credentials_file=str(credential_path.absolute()))
    if credential_path.absolute() == path.absolute():
        raise AppError("invalid_request", "Configuration and credentials must use separate files.")
    if args.replace_password or not profile.password:
        if entered is None:
            if not interactive:
                raise AppError("setup_incomplete", f"No credential is available. Run uvx imap-agent-cli setup --profile {name} in your terminal or provide a password environment variable. No account was saved.")
            info = PROVIDERS.get(provider_for(profile.host))
            print("\n" + (info[3] if info else "Obtain your IMAP login and password from your provider. Use an app password when available.\nCredential expiration and renewal depend on your provider."), file=sys.stderr)
            if info:
                print(f"\nCredential creation:\n  {info[2]}", file=sys.stderr)
            print(f"\nThe credential may grant broader mail access than this tool uses.\nIt will be saved as plain text with inherited permissions:\n  {credential_path}\n\nPaste your password, then press Enter. Input is masked with asterisks.", file=sys.stderr)
            entered = read_masked_password(sys.stdin, sys.stderr)
            profile = replace(profile, password=entered, credential_source="input")
    if not profile.password:
        raise AppError("auth_failed", "No password was entered. No account was saved.")
    print(f"\nChecking {profile.host}:{profile.port} and read-only mailbox access...", file=sys.stderr)
    report = verify_profile(profile, defaults)
    report.update(config_path=str(path), credentials_path=str(credential_path), ready=False,
                  account_saved=False, credential_saved=False)
    if not report["ok"]:
        return write_report(report, args.format)
    if profile.credential_source.startswith("environment:"):
        print("Using an environment credential. Its value will not be saved. The agent must inherit the same variable.", file=sys.stderr)
    old_profile = config.profiles.get(name)
    staged_id = None
    try:
        if entered is not None:
            profile = save_credential(profile, credential_path)
            staged_id = profile.credential_id
            report["credential_saved"] = True
        save_profile(profile, path=path, expected=expected, defaults=defaults if args.default_folder else None,
                     set_default=args.set_default)
        report["account_saved"] = True
        report["profile"]["credential_source"] = profile.credential_source
    except AppError as exc:
        report["next_steps"] = [exc.message, "Setup is incomplete. Existing account configuration was preserved. You can supply a password environment variable in the agent execution environment."]
        if staged_id:
            try:
                remove_credential(staged_id, credential_path)
                report["credential_saved"] = False
            except AppError:
                report["next_steps"].append("A staged credential remains in the credentials file. Its value was not displayed.")
        return write_report(report, args.format)
    if staged_id and old_profile and old_profile.credential_id and not any(
            p.credential_id == old_profile.credential_id for n, p in config.profiles.items() if n != name) and credentials_path(path, old_profile, overrides=False).absolute() == credential_path.absolute():
        try:
            remove_credential(old_profile.credential_id, credentials_path(path, old_profile))
        except AppError:
            report["next_steps"] = ["The replacement works, but the previous credential record could not be removed. Check credential file access in your terminal."]
    if args.no_skill:
        report["skill"] = {"installed": False, "status": "skipped"}
    else:
        try:
            report["skill"] = install_skill(Path(args.skills_dir).expanduser() if args.skills_dir else None)
        except (AppError, OSError, UnicodeError):
            report["skill"] = inspect_skill(args.skills_dir)
            report["next_steps"] = ["The account is saved, but skill installation did not complete. Existing skill content was preserved. Run uvx imap-agent-cli skill status --format plain with the same skills directory, then use an explicit skill install command."]
            return write_report(report, args.format)
    report["ready"] = True
    return write_report(report, args.format)
