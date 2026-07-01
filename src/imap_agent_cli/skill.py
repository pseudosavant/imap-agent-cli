from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import AppError


SKILL_NAME = "imap"
MANAGED_MARKER = "<!-- managed-by: imap-agent-cli -->"


SKILL_MD = f"""---
name: imap
description: Use when the user asks an agentic tool to search, read, summarize, inspect, or draft email using imap-agent-cli. Covers safe IMAP-only email access, folder enumeration, attachment download, and draft creation; must not send, delete, move, archive, flag, label, or mark messages read/unread.
---

{MANAGED_MARKER}

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
uvx --refresh-package imap-agent-cli imap-agent-cli <command>
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
uvx --refresh-package imap-agent-cli imap-agent-cli config check
```

## Folder Listing

Use this to discover folder names and Drafts naming:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli folders
```

Return only relevant folder names/counts to the user unless they ask for the full list.

## Search

Search defaults to `INBOX` when no folder is provided:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --subject "invoice" --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --from "Justin" --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --to "me@example.com" --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --message-id "<message@example.com>" --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --has-attachments --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --since 2026-01-01 --before 2026-02-01 --max-results 10
```

Search returns metadata summaries and does not fetch full message bodies. Use bounded `--max-results` and `--max-scan` values for broad searches.

Other useful filters:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --text "contract" --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --unseen --max-results 10
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder INBOX --larger 10000 --smaller 500000 --max-results 10
```

For child folders:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli search --folder Projects --recursive --max-results 25
```

For all folders, use sparingly because large mailboxes can be slow:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli search --all-folders --subject "contract" --max-results 25
```

## Read

Read a message by folder and UID from search results:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli read --folder INBOX --uid 12345 --body-format metadata
uvx --refresh-package imap-agent-cli imap-agent-cli read --folder INBOX --uid 12345 --body-format plain --max-body-chars 12000
uvx --refresh-package imap-agent-cli imap-agent-cli read --folder INBOX --uid 12345 --body-format html --include-attachments none
```

Use `metadata` first when confirming identity or headers. Use `--include-attachments none` when attachments are irrelevant, and `--include-attachments metadata` when attachment names/types/sizes matter. Use `plain` when summarizing. Use `html` only when formatting or links matter.

## Thread Context

Use thread context when preparing a reply or understanding conversation state. Start with metadata-only context:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli thread --folder INBOX --uid 12345 --max-messages 5
```

Include only the latest body when needed:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli thread --folder INBOX --uid 12345 --include-body latest --body-format plain --max-body-chars 6000
```

## Attachments

List attachment metadata:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli attachments --folder INBOX --uid 12345
```

Download requires an explicit output directory:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 1 --output-dir ./email-attachments
uvx --refresh-package imap-agent-cli imap-agent-cli attachments download --folder INBOX --uid 12345 --all --output-dir ./email-attachments
```

Report saved paths to the user.

## Drafts

Create a new draft:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli draft create --to person@example.com --subject "Subject" --body "Draft body"
```

Create a reply draft from an existing message:

```text
uvx --refresh-package imap-agent-cli imap-agent-cli draft reply --folder INBOX --uid 12345 --body "Draft reply body"
```

For longer draft bodies, write a temporary body file and pass `--body-file`. Tell the user a draft was created; do not imply it was sent.

## Output Handling

All CLI payloads are JSON on stdout. Diagnostics and errors are on stderr. Parse JSON before deciding what to show the user, and summarize high-signal fields instead of dumping raw output.
"""


def default_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def skill_dir(skills_dir: Path | None = None) -> Path:
    return (skills_dir or default_skills_dir()) / SKILL_NAME


def install_skill(skills_dir: Path | None = None) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    target.mkdir(parents=True, exist_ok=True)
    skill_path = target / "SKILL.md"
    previous = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    updated = previous != SKILL_MD
    skill_path.write_text(SKILL_MD, encoding="utf-8")
    return {
        "installed": True,
        "updated": updated,
        "skill": SKILL_NAME,
        "path": str(skill_path),
    }


def remove_skill(skills_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    skill_path = target / "SKILL.md"
    if not target.exists():
        return {"removed": False, "skill": SKILL_NAME, "path": str(target), "reason": "not_installed"}
    if not skill_path.exists():
        raise AppError("invalid_request", f"refusing to remove '{target}' because SKILL.md is missing.")
    content = skill_path.read_text(encoding="utf-8")
    if MANAGED_MARKER not in content and not force:
        raise AppError(
            "invalid_request",
            f"refusing to remove '{target}' because it is not marked as managed by imap-agent-cli; use --force to override.",
        )
    shutil.rmtree(target)
    return {"removed": True, "skill": SKILL_NAME, "path": str(target)}
