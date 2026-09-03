from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from packaging.version import InvalidVersion, Version
from yaml.nodes import MappingNode, Node, ScalarNode

from . import __version__
from .errors import AppError
from .runtime import DISTRIBUTION_NAME, is_local_development


SKILL_NAME = "imap"
MANAGED_MARKER = "<!-- managed-by: imap-agent-cli -->"
FORCE_INSTALL_COMMAND = "uvx imap-agent-cli skill install --force"
HASH_FIELD = "managed-content-sha256"


_SKILL_TEMPLATE = """---
name: imap
description: Use when the user asks an agentic tool to search, read, summarize, inspect, or draft email using imap-agent-cli. Covers safe IMAP-only email access, folder enumeration, attachment download, and draft creation; must not send, delete, move, archive, flag, label, or mark messages read/unread.
metadata:
  managed-by: imap-agent-cli
  managed-version: {managed_version}
  managed-content-sha256: ""
---

# IMAP Email Access

Use `imap-agent-cli` for safe agentic email workflows over IMAP. The tool can search and read email, list folders, inspect/download attachments, and create drafts. It cannot send email and must not be used for destructive or metadata-mutating mailbox actions.

## Safety Rules

- Never send email. There is no SMTP support.
- Never delete, move, archive, label, flag, star, mark read, or mark unread messages.
- Prefer metadata/search before reading message bodies.
- Do not print large or sensitive email bodies unless the user asks to see the content.
- Use `--body-format metadata` and `--include-attachments none` when you only need headers.
- Use `--body-format plain` for summaries unless HTML structure matters.
- Download attachments only when the user asks and provides or accepts an output directory.
- Prefer `thread --include-body none` or `thread --include-body latest` before reading multiple full messages.
- Create drafts only with `draft create` or `draft reply`.

## Command Form

Prefer the published CLI:

```text
uvx imap-agent-cli <command>
```

If working inside the `imap-agent-cli` repository, this local form is also valid:

```text
uv run ./imap_agent_cli.py <command>
```

The CLI uses these environment variables for the default profile:

```text
IMAP_AGENT_CLI_HOST
IMAP_AGENT_CLI_PORT
IMAP_AGENT_CLI_USERNAME
IMAP_AGENT_CLI_PASSWORD
IMAP_AGENT_CLI_TLS
IMAP_AGENT_CLI_SSL_MODE
IMAP_AGENT_CLI_DRAFTS_FOLDER
```

## Configuration Check

Use this first when setup may be wrong. It validates config, login, folders, default folder, and Drafts detection without reading message bodies:

```text
uvx imap-agent-cli config check
```

## Folder Listing

Use this to discover folder names and Drafts naming:

```text
uvx imap-agent-cli folders
```

Return only relevant folder names/counts to the user unless they ask for the full list.

## Search

Search defaults to `INBOX` when no folder is provided:

```text
uvx imap-agent-cli search --folder INBOX --max-results 10
uvx imap-agent-cli search --folder INBOX --subject "invoice" --max-results 10
uvx imap-agent-cli search --folder INBOX --from "Justin" --max-results 10
uvx imap-agent-cli search --folder INBOX --to "me@example.com" --max-results 10
uvx imap-agent-cli search --folder INBOX --message-id "<message@example.com>" --max-results 10
uvx imap-agent-cli search --folder INBOX --has-attachments --max-results 10
uvx imap-agent-cli search --folder INBOX --since 2026-01-01 --before 2026-02-01 --max-results 10
```

Search returns metadata summaries and does not fetch full message bodies. Use bounded `--max-results` and `--max-scan` values for broad searches.

Other useful filters:

```text
uvx imap-agent-cli search --folder INBOX --text "contract" --max-results 10
uvx imap-agent-cli search --folder INBOX --unseen --max-results 10
uvx imap-agent-cli search --folder INBOX --larger 10000 --smaller 500000 --max-results 10
```

For child folders:

```text
uvx imap-agent-cli search --folder Projects --recursive --max-results 25
```

For all folders, use sparingly because large mailboxes can be slow:

```text
uvx imap-agent-cli search --all-folders --subject "contract" --max-results 25
```

## Read

Read a message by folder and UID from search results:

```text
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format metadata
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format plain --max-body-chars 12000
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format html --include-attachments none
```

Use `metadata` first when confirming identity or headers. Use `--include-attachments none` when attachments are irrelevant, and `--include-attachments metadata` when attachment names/types/sizes matter. Use `plain` when summarizing. Use `html` only when formatting or links matter.

## Thread Context

Use thread context when preparing a reply or understanding conversation state. Start with metadata-only context:

```text
uvx imap-agent-cli thread --folder INBOX --uid 12345 --max-messages 5
```

Include only the latest body when needed:

```text
uvx imap-agent-cli thread --folder INBOX --uid 12345 --include-body latest --body-format plain --max-body-chars 6000
```

## Attachments

List attachment metadata:

```text
uvx imap-agent-cli attachments --folder INBOX --uid 12345
```

Download requires an explicit output directory:

```text
uvx imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 1 --output-dir ./email-attachments
uvx imap-agent-cli attachments download --folder INBOX --uid 12345 --all --output-dir ./email-attachments
```

Report saved paths to the user.

## Drafts

Create a new draft:

```text
uvx imap-agent-cli draft create --to person@example.com --subject "Subject" --body "Draft body"
```

Create a reply draft from an existing message:

```text
uvx imap-agent-cli draft reply --folder INBOX --uid 12345 --body "Draft reply body"
```

For longer draft bodies, write a temporary body file and pass `--body-file`. Tell the user a draft was created; do not imply it was sent.

## Output Handling

All CLI payloads are JSON on stdout. Diagnostics and errors are on stderr. Parse JSON before deciding what to show the user, and summarize high-signal fields instead of dumping raw output.
"""


def default_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def skill_dir(skills_dir: Path | None = None) -> Path:
    return (skills_dir or default_skills_dir()) / SKILL_NAME


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _mapping(node: Node | None) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise AppError("invalid_request", "skill front matter and metadata must be YAML mappings.")
    result = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in result:
            raise AppError("invalid_request", "skill metadata contains ambiguous or duplicate YAML keys.")
        # Merged mappings can hide a conflicting owner or an aliased hash value.
        if key.value == "<<":
            raise AppError("invalid_request", "merged skill metadata cannot be verified.")
        result[key.value] = value
    return result


def _front_matter(text: str) -> tuple[dict[str, Node], int]:
    """Return metadata nodes and their offset in the normalized complete file.

    Node marks let hashing blank only the hash value. Never serialize installed
    YAML, since comments, whitespace and unrelated fields are part of the hash.
    """
    opening = re.match(r"\A\ufeff?---[ \t]*\n", text)
    if opening is None:
        return {}, 0
    offset = opening.end()
    end = re.search(r"(?m)^---[ \t]*(?:\n|$)", text[offset:])
    if end is None:
        raise AppError("invalid_request", "skill YAML front matter is not closed.")
    try:
        root = _mapping(yaml.compose(text[offset:offset + end.start()], Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise AppError("invalid_request", "skill YAML front matter could not be parsed.") from exc
    if "metadata" not in root:
        return {}, offset
    return _mapping(root["metadata"]), offset


def _string(node: Node | None) -> str | None:
    if isinstance(node, ScalarNode) and node.tag == "tag:yaml.org,2002:str":
        return node.value
    return None


def _hash_span(text: str, node: Node | None, offset: int) -> tuple[int, int] | None:
    if not isinstance(node, ScalarNode):
        return None
    start, end = offset + node.start_mark.index, offset + node.end_mark.index
    # Aliases, tags and block scalars cannot be blanked without affecting other
    # YAML syntax or fields. Preserve those files as unverifiable.
    token = text[start:end]
    if node.style in ("|", ">") or token.startswith(("&", "*", "!")):
        return None
    return start, end


def _digest(text: str, span: tuple[int, int]) -> str:
    start, end = span
    empty_hash = text[:start] + '""' + text[end:]
    return "sha256:" + hashlib.sha256(_normalize(empty_hash).encode("utf-8")).hexdigest()


def render_skill() -> str:
    """Render the sole canonical skill using the exact version the CLI reports."""
    text = _normalize(_SKILL_TEMPLATE.replace("{managed_version}", json.dumps(__version__)))
    fields, offset = _front_matter(text)
    span = _hash_span(text, fields[HASH_FIELD], offset)
    assert span is not None
    start, end = span
    return text[:start] + json.dumps(_digest(text, span)) + text[end:]


# Keep the existing import available without maintaining a second text source.
SKILL_MD = render_skill()


def _version(value: str | None) -> Version | None:
    if value is None:
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _inspect(content: bytes | None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "installed": content is not None,
        "managed": False,
        "cli_version": __version__,
        "installed_version": None,
        "version_state": "not_applicable",
        "version_relation": "not_applicable",
        "integrity": "not_applicable",
        "force_install_command": None,
    }
    if content is None:
        return state
    text = _normalize(content.decode("utf-8"))
    fields, offset = _front_matter(text)
    owner = fields.get("managed-by")
    legacy = MANAGED_MARKER in text
    state["managed"] = _string(owner) == DISTRIBUTION_NAME if owner is not None else legacy
    if not state["managed"]:
        return state
    version_text = _string(fields.get("managed-version"))
    installed = _version(version_text)
    if "managed-version" not in fields:
        state["version_state"] = "legacy" if legacy else "missing"
        if legacy:
            version_text, installed = "0", Version("0")
    else:
        state["version_state"] = "valid" if installed is not None else "malformed"
    current = _version(__version__)
    state["installed_version"] = version_text if installed is not None else None
    state["version_relation"] = "unknown"
    if installed is not None and current is not None:
        state["version_relation"] = "older" if installed < current else "newer" if installed > current else "equal"

    hash_node = fields.get(HASH_FIELD)
    stored_hash = _string(hash_node)
    span = _hash_span(text, hash_node, offset)
    if state["version_state"] == "legacy":
        state["integrity"] = "legacy"
    elif hash_node is None:
        state["integrity"] = "missing"
    elif stored_hash is None or re.fullmatch(r"sha256:[0-9a-f]{64}", stored_hash) is None or span is None:
        state["integrity"] = "malformed"
    else:
        state["integrity"] = "valid" if _digest(text, span) == stored_hash else "altered"
    if state["version_state"] == "valid" and state["integrity"] != "valid":
        state["force_install_command"] = FORCE_INSTALL_COMMAND
    return state


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _read_skill(path: Path) -> bytes | None:
    if _is_link(path.parent) or _is_link(path):
        raise AppError("invalid_request", f"refusing to manage linked skill path '{path}'.")
    if path.parent.exists() and not path.parent.is_dir():
        raise AppError("invalid_request", f"skill directory '{path.parent}' is not a directory.")
    if path.exists() and not path.is_file():
        raise AppError("invalid_request", f"skill path '{path}' is not a regular file.")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, content: str, expected: bytes | None) -> bool:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".SKILL-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        # Abort on any intervening edit, including another process installing a
        # newer version. No lock or wait is needed for best-effort maintenance.
        if _read_skill(path) != expected:
            return False
        os.replace(temporary, path)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sync_skip_reason(state: dict[str, Any]) -> str | None:
    if not state["installed"]:
        return "not_installed"
    if not state["managed"]:
        return "unmanaged"
    if _version(__version__) is None:
        return "invalid_cli_version"
    # Missing or malformed versions deliberately recover without a hash check.
    if state["version_state"] in ("missing", "malformed", "legacy"):
        return None
    if state["version_relation"] in ("equal", "newer"):
        return state["version_relation"]
    if state["integrity"] != "valid":
        return "altered_or_unverifiable"
    return None


def skill_status(skills_dir: Path | None = None) -> dict[str, Any]:
    path = skill_dir(skills_dir) / "SKILL.md"
    state = _inspect(_read_skill(path))
    local = is_local_development()
    standard = path.absolute() == (skill_dir() / "SKILL.md").absolute()
    reason = "local_development" if local else "custom_directory" if not standard else _sync_skip_reason(state)
    return {
        "skill": SKILL_NAME,
        "path": str(path),
        "location": "standard" if standard else "custom",
        **state,
        "local_development": local,
        "automatic_sync_eligible": reason is None,
        "automatic_sync_skip_reason": reason,
    }


def sync_skill() -> None:
    """Best-effort local maintenance. Never install a missing skill."""
    try:
        # Check before resolving or reading the user's skill directory.
        if is_local_development() or _version(__version__) is None:
            return
        path = skill_dir() / "SKILL.md"
        previous = _read_skill(path)
        state = _inspect(previous)
        reason = _sync_skip_reason(state)
        if reason == "altered_or_unverifiable":
            print(f"imap skill at '{path}' is altered or unverifiable. To replace it, run: {FORCE_INSTALL_COMMAND}", file=sys.stderr)
        elif reason is None and _atomic_write(path, render_skill(), previous):
            old = state["installed_version"] or state["version_state"]
            print(f"Updated imap skill from {old} to {__version__} at '{path}'.", file=sys.stderr)
    except Exception:
        # Do not expose installed content through parser or decoding exceptions.
        print("Warning: imap skill synchronization failed. The command will continue.", file=sys.stderr)


def install_skill(skills_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    path = target / "SKILL.md"
    previous = _read_skill(path)
    state = _inspect(previous)
    result = {"installed": True, "updated": False, "skill": SKILL_NAME, "path": str(path)}
    if previous is not None:
        if not state["managed"]:
            raise AppError("invalid_request", f"refusing to overwrite unmanaged skill '{path}', even with --force.")
        if state["version_relation"] == "newer":
            return {**result, "reason": "newer_version"}
        if state["version_state"] == "valid" and state["integrity"] != "valid" and not force:
            raise AppError("invalid_request", f"skill '{path}' is altered or unverifiable. To replace it, run: {FORCE_INSTALL_COMMAND}")
        if state["version_relation"] == "equal" and not force:
            return result
    elif target.exists() and any(target.iterdir()):
        raise AppError("invalid_request", f"refusing to install in nonempty directory '{target}' without SKILL.md.")
    content = render_skill()
    if previous == content.encode("utf-8"):
        return result
    target.mkdir(parents=True, exist_ok=True)
    if not _atomic_write(path, content, previous):
        raise AppError("invalid_request", f"skill '{path}' changed during installation. Retry the command.")
    return {**result, "updated": True}


def remove_skill(skills_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    path = target / "SKILL.md"
    content = _read_skill(path)
    if not target.exists():
        return {"removed": False, "skill": SKILL_NAME, "path": str(target), "reason": "not_installed"}
    if content is None:
        raise AppError("invalid_request", f"refusing to remove '{target}' because SKILL.md is missing.")
    if not force and not _inspect(content)["managed"]:
        raise AppError("invalid_request", f"refusing to remove '{target}' because it is not managed by imap-agent-cli. Use --force to override.")
    if _read_skill(path) != content:
        raise AppError("invalid_request", f"skill '{path}' changed during removal. Retry the command.")
    path.unlink()
    # Only SKILL.md belongs to this tool. Keep unrelated files and directories.
    try:
        target.rmdir()
    except OSError:
        pass
    return {"removed": True, "skill": SKILL_NAME, "path": str(target)}
