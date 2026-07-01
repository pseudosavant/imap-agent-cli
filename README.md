# imap-agent-cli

`imap-agent-cli` is an agent-first IMAP CLI for safe email search, read, attachment download, and draft creation.

It can inspect mailboxes and append messages to Drafts. It cannot send email, delete messages, move messages, archive messages, change labels, alter flags, or mark messages read/unread.

## Using imap-agent-cli

### Quick Start

Install or update the `imap` agent skill:

```text
uvx imap-agent-cli install-skill
```

Configure the default IMAP account with environment variables:

```text
IMAP_AGENT_CLI_HOST=imap.example.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=me@example.com
IMAP_AGENT_CLI_PASSWORD=your-password-or-app-password
IMAP_AGENT_CLI_TLS=true
```

Then ask your agentic tool to use the `imap` skill for mailbox search, email reading, attachment inspection/download, or draft creation.

### Use From PyPI

Use the published CLI directly with `uvx`:

```text
uvx imap-agent-cli --help
uvx imap-agent-cli folders
uvx imap-agent-cli search --subject invoice
```

Install it as a persistent `uv` tool when you want to call `imap-agent-cli` directly:

```text
uv tool install imap-agent-cli
uv tool update-shell
imap-agent-cli --help
```

Restart your shell after `uv tool update-shell` if `imap-agent-cli` is not found.

Or install it into a Python environment:

```text
python -m pip install imap-agent-cli
imap-agent-cli --help
```

All command payloads are JSON on stdout. Diagnostics and errors go to stderr.

Use `imap-agent-cli --about` for project URL and license attribution. Use `imap-agent-cli --version` to print only the version number.

### Configuration

For a single account, environment variables are enough:

```text
IMAP_AGENT_CLI_HOST=imap.example.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=me@example.com
IMAP_AGENT_CLI_PASSWORD=your-password-or-app-password
IMAP_AGENT_CLI_TLS=true
IMAP_AGENT_CLI_DRAFTS_FOLDER=Drafts
```

`IMAP_AGENT_CLI_DRAFTS_FOLDER` is optional. When omitted, the CLI tries to auto-detect the Drafts folder.

For multiple accounts, create a config file:

```text
imap-agent-cli config init
imap-agent-cli config add-profile work --host imap.example.com --port 993 --username me@example.com --password-env IMAP_AGENT_CLI_WORK_PASSWORD
imap-agent-cli config set-default-profile work
```

The config file is stored at:

```text
~/.imap-agent-cli/config.toml
```

Keep secrets in environment variables. The config file should reference password environment variable names, not contain passwords.

### Agent Skill

The installed skill teaches an agentic tool how to use `imap-agent-cli` safely and effectively.

Install or update the user-scoped `imap` skill:

```text
uvx imap-agent-cli install-skill
```

This writes:

```text
~/.agents/skills/imap/SKILL.md
```

Remove the managed skill:

```text
uvx imap-agent-cli remove-skill
```

Use `--skills-dir PATH` to install into a nonstandard skills directory:

```text
uvx imap-agent-cli install-skill --skills-dir ~/.agents/skills
```

### Safety Boundary

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

### Example Commands

These examples are mostly useful for validating configuration or debugging what an agentic tool is doing:

```text
imap-agent-cli folders
imap-agent-cli search --folder INBOX --subject "invoice" --max-results 10
imap-agent-cli read --folder INBOX --uid 12345 --body-format html
imap-agent-cli attachments --folder INBOX --uid 12345
imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 2 --output-dir ./email-attachments
imap-agent-cli draft create --to person@example.com --subject "Hello" --body "Draft only."
imap-agent-cli draft reply --folder INBOX --uid 12345 --body "Thanks. I will review this."
```

## Development

### Local Development

Run the CLI from the repository:

```text
uv run ./imap_agent_cli.py --help
uv run ./imap_agent_cli.py folders
uv run ./imap_agent_cli.py search --subject invoice
```

Build the package:

```text
uv build --no-sources
```

### Testing

Run no-network unit tests:

```text
python -m unittest discover -v
```

Run the opt-in local IMAP integration test:

Set `IMAP_AGENT_CLI_TEST_PYMAP=1` in your shell, then run:

```text
uv run --extra test python -m unittest tests.test_pymap_integration -v
```

The integration test starts `pymap dict --demo-data` locally and logs in with `demouser` / `demopass`.

### Project Status

This repo is under active development. The behavior target is defined in [`spec.md`](./spec.md).

### Publishing

The GitHub Actions workflow is `.github/workflows/publish.yml`.

Publishing runs when a version tag such as `0.1.4` is pushed. The workflow can also be run manually.

PyPI Trusted Publishing values:

```text
Project: imap-agent-cli
Owner: pseudosavant
Repository: imap-agent-cli
Workflow: publish.yml
Environment: pypi
```
