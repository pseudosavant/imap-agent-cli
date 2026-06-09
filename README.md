# imap-agent-cli

`imap-agent-cli` is an agent-first IMAP CLI for safe email search, read, attachment download, and draft creation.

It can inspect mailboxes and append messages to Drafts. It cannot send email, delete messages, move messages, archive messages, change labels, alter flags, or mark messages read/unread.

## Status

This repo is under active development. The behavior target is defined in [`spec.md`](./spec.md).

## Quick Start

Single-profile env-var setup:

```text
IMAP_AGENT_CLI_HOST=imap.example.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=me@example.com
IMAP_AGENT_CLI_PASSWORD=...
IMAP_AGENT_CLI_TLS=true
```

Local development:

```text
uv run ./imap_agent_cli.py --help
uv run ./imap_agent_cli.py folders
uv run ./imap_agent_cli.py search --subject invoice
```

Packaged execution:

```text
uvx imap-agent-cli --help
```

## Examples

```text
imap-agent-cli folders
imap-agent-cli search --folder INBOX --subject "invoice" --max-results 10
imap-agent-cli read --folder INBOX --uid 12345 --body-format html
imap-agent-cli attachments --folder INBOX --uid 12345
imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 2 --output-dir ./email-attachments
imap-agent-cli draft create --to person@example.com --subject "Hello" --body "Draft only."
imap-agent-cli draft reply --folder INBOX --uid 12345 --body "Thanks. I will review this."
```

All command payloads are JSON on stdout. Diagnostics and errors go to stderr.

## Config

Create a starter config:

```text
imap-agent-cli config init
```

Add a named profile:

```text
imap-agent-cli config add-profile work --host imap.example.com --port 993 --username me@example.com --password-env IMAP_AGENT_CLI_WORK_PASSWORD
imap-agent-cli config set-default-profile work
```

Config path:

```text
~/.imap-agent-cli/config.toml
```

Secrets should stay in environment variables, not the config file.

## Agent Skill

Install or update the user-scoped `imap` skill:

```text
imap-agent-cli install-skill
```

This writes:

```text
~/.agents/skills/imap/SKILL.md
```

Remove the managed skill:

```text
imap-agent-cli remove-skill
```

Use `--skills-dir PATH` to install into a nonstandard skills directory, for example:

```text
imap-agent-cli install-skill --skills-dir ~/.agents/skills
```

## Safety Boundary

Allowed:

- list folders
- search messages
- read messages with no-seen fetch behavior
- list/download attachments only when requested
- append new messages to Drafts

Not allowed:

- send
- delete
- move
- archive
- flag/star
- mark read/unread
- create/delete/rename folders

## Testing

No-network unit tests:

```text
python -m unittest discover -v
```

Package build:

```text
uv build --no-sources
```

The spec targets `pymap` for future local IMAP integration tests on Windows without Docker.

Opt-in local IMAP integration test:

```text
$env:IMAP_AGENT_CLI_TEST_PYMAP = "1"
uv run --extra test python -m unittest tests.test_pymap_integration -v
```

The integration test starts `pymap dict --demo-data` locally and logs in with `demouser` / `demopass`.

## Publishing

The GitHub Actions workflow is `.github/workflows/publish.yml`.

PyPI Trusted Publishing values:

```text
Project: imap-agent-cli
Owner: pseudosavant
Repository: imap-agent-cli
Workflow: publish.yml
Environment: pypi
```
