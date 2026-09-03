# imap-agent-cli

`imap-agent-cli` is an agent-first IMAP CLI for safe email search, read, attachment download, and draft creation.

It can inspect mailboxes and append messages to Drafts. It cannot send email, delete messages, move messages, archive messages, change labels, alter flags, or mark messages read/unread.

## Using imap-agent-cli

### Quick Start

Install or update the `imap` agent skill:

```text
uvx imap-agent-cli skill install
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

Command payloads are JSON on stdout by default. Skill status also offers `--format plain`. Diagnostics and errors go to stderr.

Use `imap-agent-cli --about` for project URL and license attribution. Use `imap-agent-cli --version` to print only the version number.

### Configuration

For a single account, environment variables are enough:

```text
IMAP_AGENT_CLI_HOST=imap.example.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=me@example.com
IMAP_AGENT_CLI_PASSWORD=your-password-or-app-password
IMAP_AGENT_CLI_TLS=true
IMAP_AGENT_CLI_SSL_MODE=required
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

Validate setup without reading message bodies:

```text
imap-agent-cli config check
```

### Agent Skill

The installed skill teaches an agentic tool how to use `imap-agent-cli` safely through `uvx`.

Install or update the user-scoped `imap` skill:

```text
uvx imap-agent-cli skill install
```

This writes:

```text
~/.agents/skills/imap/SKILL.md
```

Normally installed CLI builds check this location on every ordinary invocation, including help, version, and about output. An already-installed managed skill from an older CLI version is replaced only if its recorded content hash still matches. Missing skills are never installed automatically. Unmanaged skills, equal versions, and newer versions are left alone.

The running CLI version is the authority. Synchronization is local. It does not query PyPI, refresh uv's cache, or update uv or the CLI. Updating those tools remains a separate action. Skill-management commands skip automatic synchronization.

Inspect the installed version, content integrity, and synchronization eligibility without changing the skill:

```text
uvx imap-agent-cli skill status
uvx imap-agent-cli skill status --format plain
```

Modified skills and skills with valid versions but missing or invalid hashes are preserved. To replace managed content explicitly:

```text
uvx imap-agent-cli skill install --force
```

Install-time `--force` still refuses unmanaged content and never downgrades a newer skill. Legacy skills with the old HTML managed marker migrate once as version 0. Missing or malformed managed versions receive a fresh replacement without a hash check. Ownership, version, and the SHA-256 content hash are stored in `SKILL.md` front matter under `metadata`. The hash detects edits. It is not a signature.

Remove the managed skill:

```text
uvx imap-agent-cli skill remove
```

Removal deletes only `SKILL.md` and removes its directory if empty. Unrelated files are kept. Removal retains `--force` for explicitly removing an unmanaged `SKILL.md`. The original `install-skill` and `remove-skill` aliases remain supported.

Use `--skills-dir PATH` on any skill command to select a custom skills root. Custom locations require explicit updates and are never discovered by ordinary invocations:

```text
uvx imap-agent-cli skill install --skills-dir ./my-skills
uvx imap-agent-cli skill status --skills-dir ./my-skills
```

Local checkouts, local source installs, and editable builds do not synchronize automatically. Unverifiable installation origins are also skipped. Explicit installation still works from development builds, including `uvx --from . imap-agent-cli skill install`. Installed wheels remain eligible, including wheels installed from a local file.

An update affects future agent skill loading. An agent session may retain instructions it has already loaded.

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
imap-agent-cli search --folder INBOX --from "Justin" --to "me@example.com" --max-results 10
imap-agent-cli search --folder INBOX --has-attachments --max-results 10 --max-scan 100
imap-agent-cli read --folder INBOX --uid 12345 --body-format html --include-attachments none
imap-agent-cli thread --folder INBOX --uid 12345 --include-body latest --body-format plain
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

To validate an installed wheel and local-source exclusions, build the package and set `IMAP_AGENT_CLI_WHEEL_TEST` to the wheel's path. Then run:

```text
python -m unittest tests.test_wheel_smoke -v
```

This opt-in check uses uv to install into a temporary environment with a temporary home directory. The regular tests also isolate skill paths from your home directory.

Run the opt-in local IMAP integration test:

Set `IMAP_AGENT_CLI_TEST_PYMAP=1` in your shell, then run:

```text
uv run --extra test python -m unittest tests.test_pymap_integration -v
```

The integration test starts `pymap dict --demo-data` locally and logs in with `demouser` / `demopass`.

Run the opt-in live IMAP no-seen test only when you intentionally want to validate the configured mailbox:

```text
python -m unittest tests.test_live_no_seen -v
```

Set `IMAP_AGENT_CLI_LIVE_TEST=1` in your shell first. The test reads one unread message with no-seen fetch behavior and verifies the flags do not change. If no unread message exists, it skips.

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
