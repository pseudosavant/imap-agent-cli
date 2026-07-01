from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import add_profile, config_status, init_config, load_config, remove_profile, resolve_profile, set_default_profile
from .errors import AppError, ConfigError
from .imap_client import ImapSession
from .mime import create_draft_message, header_value, parse_message
from .render import write_error, write_json
from .skill import install_skill, remove_skill


PROJECT_URL = "https://github.com/pseudosavant/imap-agent-cli"
LICENSE_NAME = "MIT"

TOP_LEVEL_HELP = f"""imap-agent-cli - safe IMAP email access for agentic tools

Safety:
  Can: list folders, search, read without marking read, inspect/download attachments, create Drafts
  Cannot: send, delete, move, archive, label, flag, mark read/unread

Setup:
  Required env vars for the default profile:
    IMAP_AGENT_CLI_HOST
    IMAP_AGENT_CLI_PORT
    IMAP_AGENT_CLI_USERNAME
    IMAP_AGENT_CLI_PASSWORD
    IMAP_AGENT_CLI_TLS
    IMAP_AGENT_CLI_SSL_MODE

Common workflows:
  imap-agent-cli folders
  imap-agent-cli search --folder INBOX --from "Justin" --max-results 10
  imap-agent-cli search --folder INBOX --to "me@example.com" --has-attachments --max-results 10
  imap-agent-cli read --folder INBOX --uid 12345 --body-format metadata
  imap-agent-cli read --folder INBOX --uid 12345 --body-format html --include-attachments none --max-body-chars 12000
  imap-agent-cli thread --folder INBOX --uid 12345 --include-body latest
  imap-agent-cli attachments --folder INBOX --uid 12345
  imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 2 --output-dir ./email-attachments
  imap-agent-cli draft create --to person@example.com --subject "Subject" --body-file ./draft.txt
  imap-agent-cli draft reply --folder INBOX --uid 12345 --body-file ./reply.txt

Output:
  stdout is JSON payload only for operational commands
  stderr is diagnostics/errors only

Commands:
  config          initialize and manage profile configuration
  profiles        list configured profile names
  install-skill   install or update the imap agent skill
  remove-skill    remove the managed imap agent skill
  folders         list folders
  search          search messages
  read            read a message by folder and UID
  thread          inspect bounded thread context
  attachments     list or download attachments
  draft           create new or reply drafts

More help:
  imap-agent-cli <command> --help
  imap-agent-cli --about
  imap-agent-cli --version

Project:
  {PROJECT_URL}
License:
  {LICENSE_NAME}
"""

ABOUT_TEXT = f"""imap-agent-cli {__version__}

Safe IMAP email access for agentic tools.

Project: {PROJECT_URL}
License: {LICENSE_NAME}
"""


def _read_json_arg(value: str) -> dict[str, Any]:
    if value == "-":
        text = sys.stdin.read()
    else:
        text = Path(value).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError("invalid_request", f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AppError("invalid_request", "JSON input must be an object.")
    return payload


def _read_password(args: argparse.Namespace) -> str | None:
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")
    return None


def _common_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--tls", dest="tls", action="store_true", default=None)
    parser.add_argument("--no-tls", dest="tls", action="store_false")
    parser.add_argument("--ssl-mode", choices=["required", "preferred", "disabled"])


def _session(args: argparse.Namespace) -> ImapSession:
    config = load_config()
    profile = resolve_profile(
        config,
        getattr(args, "profile", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        username=getattr(args, "username", None),
        password=_read_password(args),
        tls=getattr(args, "tls", None),
        ssl_mode=getattr(args, "ssl_mode", None),
    )
    return ImapSession(profile, config.defaults)


def _max_body_chars(args: argparse.Namespace, payload: dict[str, Any] | None = None) -> int:
    if getattr(args, "max_body_chars", None):
        return int(args.max_body_chars)
    if payload and payload.get("max_body_chars"):
        return int(payload["max_body_chars"])
    return load_config().defaults.max_body_chars


def _include_attachments(value: Any) -> str:
    text = str(value or "metadata").strip().lower()
    if text in {"false", "none", "no", "0"}:
        return "none"
    if text in {"true", "metadata", "yes", "1"}:
        return "metadata"
    raise AppError("invalid_request", "--include-attachments must be none or metadata.")


def _check_item(name: str, ok: bool, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "ok": ok}
    item.update(details)
    return item


def _body_from_args(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    if getattr(args, "body", None) is not None:
        return args.body
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    if "body" in payload:
        return str(payload["body"])
    raise AppError("invalid_request", "draft body is required via --body, --body-file, or JSON body.")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict) and item.get("email"):
                email = str(item["email"])
                name = str(item.get("name", "")).strip()
                items.append(f"{name} <{email}>" if name else email)
        return items
    raise AppError("invalid_request", "address fields must be strings, lists of strings, or address objects.")


def _parse_repeated_addresses(values: list[str] | None) -> list[str]:
    return values or []


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_command == "init":
        path = init_config(from_env=args.from_env)
        write_json({"created": True, "path": str(path)})
        return 0
    if args.config_command == "check":
        checks: list[dict[str, Any]] = []
        report: dict[str, Any] = {"ok": False, "checks": checks}
        try:
            config = load_config()
            checks.append(_check_item("config_loaded", True, path=str(config.path)))
            profile = resolve_profile(
                config,
                getattr(args, "profile", None),
                host=getattr(args, "host", None),
                port=getattr(args, "port", None),
                username=getattr(args, "username", None),
                password=_read_password(args),
                tls=getattr(args, "tls", None),
                ssl_mode=getattr(args, "ssl_mode", None),
            )
            report["profile"] = {
                "name": profile.name,
                "host": profile.host,
                "port": profile.port,
                "username": profile.username,
                "password_env": profile.password_env,
                "has_password": bool(profile.password),
                "tls": profile.tls,
                "ssl_mode": profile.ssl_mode,
            }
            checks.append(_check_item("profile_resolved", True))
            checks.append(_check_item("password_available", bool(profile.password), password_env=profile.password_env))
            with ImapSession(profile, config.defaults) as session:
                checks.append(_check_item("login", True, security=session.security))
                capabilities = session.capabilities()
                checks.append(_check_item("capabilities", True, values=capabilities))
                folders = session.folders()["folders"]
                checks.append(_check_item("folders", True, count=len(folders)))
                default_folder = config.defaults.default_folder
                selectable = {str(folder["name"]) for folder in folders if folder.get("selectable")}
                default_ok = default_folder in selectable
                checks.append(_check_item("default_folder", default_ok, folder=default_folder))
                if default_ok:
                    session._select(default_folder, readonly=True)
                try:
                    drafts_folder = session.resolve_drafts_folder()
                    checks.append(_check_item("drafts_folder", True, folder=drafts_folder))
                except AppError as exc:
                    checks.append(_check_item("drafts_folder", False, error=exc.message))
        except AppError as exc:
            checks.append(_check_item(exc.code, False, error=exc.message))
        report["ok"] = all(item["ok"] for item in checks)
        write_json(report)
        return 0 if report["ok"] else 1
    if args.config_command == "show":
        write_json(config_status(load_config()))
        return 0
    if args.config_command == "set-default-profile":
        path = set_default_profile(args.name)
        write_json({"updated": True, "path": str(path), "default_profile": args.name})
        return 0
    if args.config_command == "add-profile":
        path = add_profile(
            args.name,
            host=args.host,
            port=args.port,
            username=args.username,
            password_env=args.password_env,
            tls=args.tls,
            ssl_mode=args.ssl_mode,
            drafts_folder=args.drafts_folder or "",
        )
        write_json({"updated": True, "path": str(path), "profile": args.name})
        return 0
    if args.config_command == "remove-profile":
        path = remove_profile(args.name)
        write_json({"updated": True, "path": str(path), "removed_profile": args.name})
        return 0
    raise AppError("invalid_request", "unsupported config command.")


def cmd_profiles(args: argparse.Namespace) -> int:
    config = load_config()
    write_json({"profiles": [profile.name for profile in config.profiles.values()], "default": config.defaults.profile})
    return 0


def cmd_install_skill(args: argparse.Namespace) -> int:
    write_json(install_skill(Path(args.skills_dir) if args.skills_dir else None))
    return 0


def cmd_remove_skill(args: argparse.Namespace) -> int:
    write_json(remove_skill(Path(args.skills_dir) if args.skills_dir else None, force=args.force))
    return 0


def cmd_folders(args: argparse.Namespace) -> int:
    with _session(args) as session:
        write_json(session.folders())
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = _read_json_arg(args.json_input) if args.json_input else {}
    config = load_config()
    folder = args.folder or payload.get("folder") or config.defaults.default_folder
    scope = payload.get("scope") or ("all" if args.all_folders else "recursive" if args.recursive else "folder")
    max_results = int(args.max_results or payload.get("max_results") or config.defaults.max_results)
    max_scan = int(args.max_scan or payload.get("max_scan") or config.defaults.max_scan)
    with _session(args) as session:
        write_json(
            session.search(
                folder=folder,
                scope=str(scope),
                subject=args.subject or payload.get("subject"),
                sender=args.sender or payload.get("from"),
                recipient=args.recipient or payload.get("to"),
                message_id=args.message_id or payload.get("message_id"),
                text=args.text or payload.get("text"),
                since=args.since or payload.get("since"),
                before=args.before or payload.get("before"),
                unseen=bool(args.unseen or payload.get("unseen", False)),
                seen=bool(args.seen or payload.get("seen", False)),
                answered=bool(args.answered or payload.get("answered", False)),
                flagged=bool(args.flagged or payload.get("flagged", False)),
                larger=args.larger or payload.get("larger"),
                smaller=args.smaller or payload.get("smaller"),
                has_attachments=bool(args.has_attachments or payload.get("has_attachments", False)),
                max_results=max_results,
                max_scan=max_scan,
                sort=str(args.sort or payload.get("sort") or "uid"),
                order=str(args.order or payload.get("order") or "desc"),
            )
        )
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = _read_json_arg(args.json_input) if args.json_input else {}
    folder = args.folder or payload.get("folder")
    uid = args.uid or payload.get("uid")
    if not folder or uid is None:
        raise AppError("invalid_request", "read requires --folder and --uid.")
    config = load_config()
    body_format = args.body_format or payload.get("body_format") or config.defaults.body_format
    max_body_chars = int(args.max_body_chars or payload.get("max_body_chars") or config.defaults.max_body_chars)
    include_attachments = _include_attachments(args.include_attachments or payload.get("include_attachments") or "metadata")
    with _session(args) as session:
        write_json(
            session.read(
                folder=str(folder),
                uid=int(uid),
                body_format=str(body_format),
                max_body_chars=max_body_chars,
                include_attachments=include_attachments,
            )
        )
    return 0


def cmd_thread(args: argparse.Namespace) -> int:
    if not args.folder or args.uid is None:
        raise AppError("invalid_request", "thread requires --folder and --uid.")
    config = load_config()
    max_body_chars = int(args.max_body_chars or config.defaults.max_body_chars)
    max_scan = int(args.max_scan or config.defaults.max_scan)
    with _session(args) as session:
        write_json(
            session.thread(
                folder=args.folder,
                uid=args.uid,
                max_messages=args.max_messages,
                max_scan=max_scan,
                include_body=args.include_body,
                body_format=args.body_format,
                max_body_chars=max_body_chars,
            )
        )
    return 0


def cmd_attachments(args: argparse.Namespace) -> int:
    if not args.folder or args.uid is None:
        raise AppError("invalid_request", "attachments requires --folder and --uid.")
    with _session(args) as session:
        write_json(session.attachments(folder=args.folder, uid=args.uid))
    return 0


def cmd_attachments_download(args: argparse.Namespace) -> int:
    if not args.all and not args.part_id:
        raise AppError("invalid_request", "attachment download requires --part-id or --all.")
    with _session(args) as session:
        write_json(
            session.download_attachments(
                folder=args.folder,
                uid=args.uid,
                output_dir=Path(args.output_dir),
                part_id=args.part_id,
                all_parts=args.all,
                include_inline=args.include_inline,
                overwrite=args.overwrite,
            )
        )
    return 0


def cmd_draft_create(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = _read_json_arg(args.json_input) if args.json_input else {}
    body = _body_from_args(args, payload)
    to = _parse_repeated_addresses(args.to) or _string_list(payload.get("to"))
    if not to:
        raise AppError("invalid_request", "draft create requires at least one recipient.")
    config = load_config()
    body_format = args.body_format or payload.get("body_format") or "plain"
    attachments = [Path(path) for path in (args.attachment or payload.get("attachments") or [])]
    with _session(args) as session:
        message = create_draft_message(
            sender=session.profile.username,
            to=to,
            cc=_parse_repeated_addresses(args.cc) or _string_list(payload.get("cc")),
            bcc=_parse_repeated_addresses(args.bcc) or _string_list(payload.get("bcc")),
            subject=args.subject or payload.get("subject") or "",
            body=body,
            body_format=str(body_format),
            attachments=attachments,
        )
        write_json(session.append_draft(message, drafts_folder=args.drafts_folder or payload.get("drafts_folder")))
    return 0


def _reply_recipients(source_message: Any) -> list[str]:
    reply_to = source_message.get_all("reply-to", [])
    if reply_to:
        return _string_list([", ".join(reply_to)])
    from_values = source_message.get_all("from", [])
    return _string_list([", ".join(from_values)])


def _reply_subject(source_subject: str) -> str:
    value = source_subject.strip()
    return value if value.lower().startswith("re:") else f"Re: {value}"


def cmd_draft_reply(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = _read_json_arg(args.json_input) if args.json_input else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    folder = args.folder or source.get("folder")
    uid = args.uid or source.get("uid")
    if not folder or uid is None:
        raise AppError("invalid_request", "draft reply requires source --folder and --uid.")
    body = _body_from_args(args, payload)
    body_format = args.body_format or payload.get("body_format") or "plain"
    attachments = [Path(path) for path in (args.attachment or payload.get("attachments") or [])]
    with _session(args) as session:
        raw = session._fetch_raw(str(folder), int(uid))
        source_message = parse_message(raw)
        to = _parse_repeated_addresses(args.to) or _string_list(payload.get("to")) or _reply_recipients(source_message)
        source_message_id = header_value(source_message, "message-id")
        source_refs = header_value(source_message, "references").strip()
        references = f"{source_refs} {source_message_id}".strip()
        message = create_draft_message(
            sender=session.profile.username,
            to=to,
            cc=_parse_repeated_addresses(args.cc) or _string_list(payload.get("cc")),
            bcc=_parse_repeated_addresses(args.bcc) or _string_list(payload.get("bcc")),
            subject=args.subject or payload.get("subject") or _reply_subject(header_value(source_message, "subject")),
            body=body,
            body_format=str(body_format),
            attachments=attachments,
            in_reply_to=source_message_id or None,
            references=references or None,
        )
        write_json(session.append_draft(message, drafts_folder=args.drafts_folder or payload.get("drafts_folder")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imap-agent-cli",
        description="Safe IMAP email access for agentic tools.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--about", action="store_true", help="show project and license information and exit")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config", help="initialize and manage profile configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_init = config_sub.add_parser("init", help="create a starter config file")
    config_init.add_argument("--from-env", action="store_true")
    config_init.set_defaults(func=cmd_config)
    config_check = config_sub.add_parser("check", help="validate profile configuration and IMAP connectivity")
    _common_profile_args(config_check)
    config_check.set_defaults(func=cmd_config)
    config_show = config_sub.add_parser("show", help="show resolved config metadata without secrets")
    config_show.set_defaults(func=cmd_config)
    config_default = config_sub.add_parser("set-default-profile", help="set the default profile")
    config_default.add_argument("name")
    config_default.set_defaults(func=cmd_config)
    config_add = config_sub.add_parser("add-profile", help="add or update a named profile")
    config_add.add_argument("name")
    config_add.add_argument("--host", required=True)
    config_add.add_argument("--port", type=int, default=993)
    config_add.add_argument("--username", required=True)
    config_add.add_argument("--password-env", required=True)
    config_add.add_argument("--tls", dest="tls", action="store_true", default=True)
    config_add.add_argument("--no-tls", dest="tls", action="store_false")
    config_add.add_argument("--ssl-mode", choices=["required", "preferred", "disabled"], default="required")
    config_add.add_argument("--drafts-folder", default="")
    config_add.set_defaults(func=cmd_config)
    config_remove = config_sub.add_parser("remove-profile", help="remove a named profile from config")
    config_remove.add_argument("name")
    config_remove.set_defaults(func=cmd_config)

    profiles = sub.add_parser("profiles", help="list configured profile names")
    profiles.set_defaults(func=cmd_profiles)

    install_skill_parser = sub.add_parser("install-skill", help="install or update the imap agent skill")
    install_skill_parser.add_argument("--skills-dir")
    install_skill_parser.set_defaults(func=cmd_install_skill)

    remove_skill_parser = sub.add_parser("remove-skill", help="remove the managed imap agent skill")
    remove_skill_parser.add_argument("--skills-dir")
    remove_skill_parser.add_argument("--force", action="store_true")
    remove_skill_parser.set_defaults(func=cmd_remove_skill)

    folders = sub.add_parser("folders", help="list folders and folder metadata")
    _common_profile_args(folders)
    folders.set_defaults(func=cmd_folders)

    search = sub.add_parser("search", help="search messages by folder, subject, sender, or date range")
    _common_profile_args(search)
    search.add_argument("--json", dest="json_input")
    search.add_argument("--folder")
    search.add_argument("--recursive", action="store_true")
    search.add_argument("--all-folders", action="store_true")
    search.add_argument("--subject")
    search.add_argument("--from", dest="sender")
    search.add_argument("--to", dest="recipient")
    search.add_argument("--message-id")
    search.add_argument("--text")
    search.add_argument("--since")
    search.add_argument("--before")
    search.add_argument("--unseen", action="store_true")
    search.add_argument("--seen", action="store_true")
    search.add_argument("--answered", action="store_true")
    search.add_argument("--flagged", action="store_true")
    search.add_argument("--larger", type=int)
    search.add_argument("--smaller", type=int)
    search.add_argument("--has-attachments", action="store_true")
    search.add_argument("--max-results", type=int)
    search.add_argument("--max-scan", type=int)
    search.add_argument("--sort", choices=["uid", "date"], default=None)
    search.add_argument("--order", choices=["asc", "desc"], default=None)
    search.set_defaults(func=cmd_search)

    read = sub.add_parser("read", help="read a message by folder and UID without marking it read")
    _common_profile_args(read)
    read.add_argument("--json", dest="json_input")
    read.add_argument("--folder")
    read.add_argument("--uid", type=int)
    read.add_argument("--body-format", choices=["html", "markdown", "plain", "raw-html", "metadata"])
    read.add_argument("--max-body-chars", type=int)
    read.add_argument("--include-attachments", choices=["none", "metadata", "false", "true"])
    read.set_defaults(func=cmd_read)

    thread = sub.add_parser("thread", help="inspect bounded thread context for a message")
    _common_profile_args(thread)
    thread.add_argument("--folder", required=True)
    thread.add_argument("--uid", required=True, type=int)
    thread.add_argument("--max-messages", type=int, default=5)
    thread.add_argument("--max-scan", type=int)
    thread.add_argument("--include-body", choices=["none", "latest", "all"], default="none")
    thread.add_argument("--body-format", choices=["html", "markdown", "plain", "raw-html", "metadata"], default="metadata")
    thread.add_argument("--max-body-chars", type=int)
    thread.set_defaults(func=cmd_thread)

    attachments = sub.add_parser("attachments", help="list attachment metadata for a message")
    _common_profile_args(attachments)
    attachments.add_argument("--folder")
    attachments.add_argument("--uid", type=int)
    attachments.set_defaults(func=cmd_attachments)
    attachments_sub = attachments.add_subparsers(dest="attachment_command")
    download = attachments_sub.add_parser("download", help="download selected attachments to a directory")
    _common_profile_args(download)
    download.add_argument("--folder", required=True)
    download.add_argument("--uid", required=True, type=int)
    download.add_argument("--part-id")
    download.add_argument("--all", action="store_true")
    download.add_argument("--output-dir", required=True)
    download.add_argument("--include-inline", action="store_true")
    download.add_argument("--overwrite", action="store_true")
    download.set_defaults(func=cmd_attachments_download)

    draft = sub.add_parser("draft", help="create new or reply drafts")
    draft_sub = draft.add_subparsers(dest="draft_command", required=True)
    create = draft_sub.add_parser("create", help="append a new message to Drafts")
    _common_profile_args(create)
    create.add_argument("--json", dest="json_input")
    create.add_argument("--to", action="append")
    create.add_argument("--cc", action="append")
    create.add_argument("--bcc", action="append")
    create.add_argument("--subject")
    create.add_argument("--body")
    create.add_argument("--body-file")
    create.add_argument("--body-format", choices=["html", "markdown", "plain"], default=None)
    create.add_argument("--attachment", action="append")
    create.add_argument("--drafts-folder")
    create.set_defaults(func=cmd_draft_create)

    reply = draft_sub.add_parser("reply", help="append a reply draft for an existing message")
    _common_profile_args(reply)
    reply.add_argument("--json", dest="json_input")
    reply.add_argument("--folder")
    reply.add_argument("--uid", type=int)
    reply.add_argument("--to", action="append")
    reply.add_argument("--cc", action="append")
    reply.add_argument("--bcc", action="append")
    reply.add_argument("--subject")
    reply.add_argument("--body")
    reply.add_argument("--body-file")
    reply.add_argument("--body-format", choices=["html", "markdown", "plain"], default=None)
    reply.add_argument("--attachment", action="append")
    reply.add_argument("--drafts-folder")
    reply.set_defaults(func=cmd_draft_reply)

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv in (["--help"], ["-h"]):
        sys.stdout.write(TOP_LEVEL_HELP)
        return 0
    if argv == ["--about"]:
        sys.stdout.write(ABOUT_TEXT)
        return 0
    if argv == ["--version"]:
        sys.stdout.write(f"{__version__}\n")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AppError as exc:
        write_error(exc)
        return exc.exit_code
    except ConfigError as exc:
        write_error(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        write_error(AppError("interrupted", "interrupted by user", exit_code=130))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
