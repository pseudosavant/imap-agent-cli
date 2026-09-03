# imap-agent-cli

`imap-agent-cli` gives coding agents a narrow, safe interface to generic IMAP mailboxes. It can search email, read messages without changing their unread state, inspect or download requested attachments, and save new or reply drafts.

It never sends email and cannot change existing messages or folders.

## Prerequisite

`imap-agent-cli` is designed to be used with [`uv`](https://docs.astral.sh/uv/getting-started/installation/). Install `uv` before continuing. The documented workflows and managed agent skill use `uvx` to run the tool without requiring a global installation.

## Quick start with an agent

Install the managed `imap` agent skill:

```text
uvx imap-agent-cli skill install
```

Configure a default IMAP account through environment variables:

```text
IMAP_AGENT_CLI_HOST=imap.example.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=me@example.com
IMAP_AGENT_CLI_PASSWORD=your-password-or-app-password
IMAP_AGENT_CLI_TLS=true
IMAP_AGENT_CLI_SSL_MODE=required
```

Check the connection without reading message bodies:

```text
uvx imap-agent-cli config check
```

Then use `$imap` in Codex, Claude Code, or another agentic tool that supports skills:

> Use $imap to find the five most recent emails about the Acme renewal. Summarize the latest thread and create a reply draft asking for the updated contract. Do not download attachments.

The skill teaches the agent to search before reading, keep operations bounded, download attachments only when requested, and create drafts without sending them.

## What it can do

| The tool can | The tool cannot |
| --- | --- |
| List folders and their metadata | Send email |
| Search message metadata | Delete, move, or archive messages |
| Read messages without marking them read | Mark messages read or unread |
| Inspect bounded thread context | Change flags, stars, or labels |
| List and download requested attachments | Create, rename, or delete folders |
| Append new and reply drafts to Drafts | Modify existing messages |

Draft creation only appends a new MIME message to the configured or detected Drafts folder. It never submits or sends the message.

## Use the CLI directly

Run the published CLI without installing it globally:

```text
uvx imap-agent-cli --help
uvx imap-agent-cli folders
uvx imap-agent-cli search --subject invoice
```

To install the command as a persistent tool instead:

```text
uv tool install imap-agent-cli
uv tool update-shell
imap-agent-cli --help
```

Restart your shell after `uv tool update-shell` if the command is not found.

You can also install it into a Python environment:

```text
python -m pip install imap-agent-cli
imap-agent-cli --help
```

The examples below continue to use `uvx imap-agent-cli` so they work without a global installation.

## How safe access works

The tool follows five core rules:

1. Folder access is read-only except for appending a new message to Drafts.
2. Search fetches message metadata, not complete message bodies.
3. Read and thread operations use peek behavior so they do not set the IMAP `\Seen` flag.
4. Attachment content is written only after an explicit download command and output directory.
5. Draft commands use IMAP `APPEND`. There is no SMTP client or send command.

The usual workflow is to search first, select a result by folder and UID, read only the needed context, then optionally create a draft.

## Configure accounts

### One account with environment variables

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

`IMAP_AGENT_CLI_DRAFTS_FOLDER` is optional. When omitted, the CLI tries the server's IMAP special-use metadata and common Drafts folder names.

Validate the resolved configuration, login, folders, default folder, and Drafts detection without reading message bodies:

```text
uvx imap-agent-cli config check
```

### Multiple account profiles

Create a config file when you need named accounts:

```text
uvx imap-agent-cli config init
uvx imap-agent-cli config add-profile work --host imap.example.com --port 993 --username me@example.com --password-env IMAP_AGENT_CLI_WORK_PASSWORD
uvx imap-agent-cli config set-default-profile work
```

The config file is stored at `~/.imap-agent-cli/config.toml`. Keep passwords in environment variables. Profiles store the name of the password environment variable, not the password itself.

The CLI uses standard IMAP username and password authentication. It does not provide OAuth setup. Providers that disable account-password login may require an app password.

Inspect resolved settings without exposing secrets, or list available profiles:

```text
uvx imap-agent-cli config show
uvx imap-agent-cli profiles
uvx imap-agent-cli config check --profile work
```

Pass `--profile NAME` to any mailbox command to select a non-default profile. Direct commands also accept connection flags and `--password-stdin` for one-off use.

### Connection security

The recommended configuration is port `993`, `tls=true`, and `ssl_mode=required`.

| Setting | Behavior |
| --- | --- |
| `tls=true`, `ssl_mode=required` | Use implicit TLS and fail if a secure connection cannot be established |
| `tls=false`, `ssl_mode=required` | Require STARTTLS before login |
| `ssl_mode=preferred` | Allow implicit TLS to fall back to STARTTLS without falling back to plaintext login |
| `ssl_mode=disabled` | Allow plaintext IMAP login for deliberate local testing only |

Both `required` and `preferred` refuse to send credentials over plaintext.

## Common workflows

### Find messages

Search defaults to `INBOX`, returns metadata only, and limits results to a bounded number:

```text
uvx imap-agent-cli search --subject "invoice" --max-results 10
uvx imap-agent-cli search --from "person@example.com" --since 2026-01-01 --max-results 10
uvx imap-agent-cli search --to "me@example.com" --has-attachments --max-results 10 --max-scan 100
```

Search a folder and its children, or search all selectable folders:

```text
uvx imap-agent-cli search --folder Projects --recursive --subject "contract" --max-results 25
uvx imap-agent-cli search --all-folders --subject "contract" --max-results 25
```

All-folder searches exclude folders marked as Junk or Spam by default. Use broad mailbox searches sparingly because large mailboxes can be slow.

### Read a selected message

Search results identify messages by folder and UID. Read only metadata when confirming a result, or request a body format when content is needed:

```text
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format metadata --include-attachments none
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format plain --max-body-chars 12000
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format markdown
uvx imap-agent-cli read --folder INBOX --uid 12345 --body-format html --include-attachments metadata
```

| Body format | Intended use |
| --- | --- |
| `metadata` | Return headers without a message body |
| `plain` | Summaries and ordinary text processing |
| `markdown` | Agent-friendly conversion that preserves common structure |
| `html` | Sanitized HTML when layout, tables, or links matter |
| `raw-html` | Explicit access to untrusted, unsanitized source HTML |

HTML sanitization removes active content and unsafe resource references. Markdown conversion is intentionally lossy. Attachment content is never included in a read result.

### Inspect thread context

Start with metadata-only context for a conversation:

```text
uvx imap-agent-cli thread --folder INBOX --uid 12345 --max-messages 5
```

Include only the latest body when preparing a summary or reply:

```text
uvx imap-agent-cli thread --folder INBOX --uid 12345 --include-body latest --body-format plain --max-body-chars 6000
```

### Inspect and download attachments

List attachment names, types, sizes, and part IDs without downloading content:

```text
uvx imap-agent-cli attachments --folder INBOX --uid 12345
```

Download one attachment or all non-inline attachments to an explicit directory:

```text
uvx imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 2 --output-dir ./email-attachments
uvx imap-agent-cli attachments download --folder INBOX --uid 12345 --all --output-dir ./email-attachments
```

Downloads sanitize filenames and preflight every target before writing. Existing files are preserved unless `--overwrite` is provided. Inline attachments are excluded unless `--include-inline` is provided.

### Create drafts

Create a new draft:

```text
uvx imap-agent-cli draft create --to person@example.com --subject "Hello" --body "Draft only."
```

Create a reply draft from an existing message:

```text
uvx imap-agent-cli draft reply --folder INBOX --uid 12345 --body "Thanks. I will review this."
```

For longer content, use `--body-file`. Add local files with repeated `--attachment` options. Draft bodies may be `plain`, `markdown`, or `html`.

Reply drafts choose the original sender or `Reply-To`, add a reply subject when needed, and preserve `In-Reply-To` and `References` headers. They do not quote the original message by default.

## Manage the agent skill

The managed skill is installed at `~/.agents/skills/imap/SKILL.md`:

```text
uvx imap-agent-cli skill install
```

Inspect its path, ownership, version, integrity, and automatic synchronization eligibility without changing it:

```text
uvx imap-agent-cli skill status
uvx imap-agent-cli skill status --format plain
```

Normal invocations of an installed CLI update a pristine older managed skill to the running CLI version. Synchronization is local. It does not query PyPI, refresh uv's cache, or update the CLI. Missing skills are never installed automatically. Unmanaged, modified, equal-version, and newer skills are preserved.

Restore altered managed content explicitly:

```text
uvx imap-agent-cli skill install --force
```

Install-time `--force` still refuses unmanaged content and never downgrades a newer skill. Managed front matter records ownership, version, and a SHA-256 content hash. The hash detects edits. It is not a signature.

Remove the managed skill:

```text
uvx imap-agent-cli skill remove
```

Removal deletes only `SKILL.md` and removes its directory when empty. Unrelated files are kept. The original `install-skill` and `remove-skill` aliases remain supported.

All skill commands accept `--skills-dir PATH`. Custom locations require explicit updates and are not checked during normal invocations:

```text
uvx imap-agent-cli skill install --skills-dir ./my-skills
uvx imap-agent-cli skill status --skills-dir ./my-skills
```

Local checkouts, direct source installs, editable builds, and unidentifiable installation origins do not synchronize automatically. Explicit skill installation still works from development builds. Skill updates affect future agent sessions and may not replace instructions already loaded by a running session.

## Structured output and automation

Operational commands write JSON payloads to stdout. Diagnostics, warnings, progress, and errors go to stderr. This keeps command output safe to parse in agent and automation workflows.

Search, read, and draft commands also accept structured input from a JSON file:

```text
uvx imap-agent-cli search --json query.json
uvx imap-agent-cli read --json message.json
uvx imap-agent-cli draft create --json draft.json
uvx imap-agent-cli draft reply --json reply.json
```

Use `--json -` to read the JSON request from stdin. Failures leave stdout empty and write a compact JSON error to stderr. Logs do not include passwords, message bodies, or attachment content.

Default guardrails keep work bounded:

| Limit | Default |
| --- | ---: |
| Search results | 25 |
| Messages scanned | 250 |
| Body characters | 12,000 |
| Thread messages | 5 |
| Connection timeout | 15 seconds |
| Read timeout | 30 seconds |

Commands expose targeted overrides such as `--max-results`, `--max-scan`, `--max-messages`, and `--max-body-chars`.

## Reference

| Command | Purpose |
| --- | --- |
| `config` | Initialize, inspect, validate, and manage profile configuration |
| `profiles` | List configured profile names |
| `skill` | Install, inspect, or remove the managed agent skill |
| `folders` | List folders and folder metadata |
| `search` | Search message metadata with bounded results |
| `read` | Read one message by folder and UID without marking it read |
| `thread` | Inspect bounded conversation context |
| `attachments` | List or explicitly download attachments |
| `draft create` | Append a new message to Drafts |
| `draft reply` | Append a reply draft with conversation headers |

Use command help for the complete set of options:

```text
uvx imap-agent-cli --help
uvx imap-agent-cli search --help
uvx imap-agent-cli read --help
uvx imap-agent-cli attachments download --help
uvx imap-agent-cli draft reply --help
uvx imap-agent-cli --about
uvx imap-agent-cli --version
```

## Development

Run the CLI from the repository:

```text
uv run ./imap_agent_cli.py --help
uv run ./imap_agent_cli.py folders
uv run ./imap_agent_cli.py search --subject invoice
```

Run the no-network test suite:

```text
python -m unittest discover -v
```

Run the opt-in local IMAP integration test after setting `IMAP_AGENT_CLI_TEST_PYMAP=1`:

```text
uv run --extra test python -m unittest tests.test_pymap_integration -v
```

The integration test starts `pymap dict --demo-data` locally and signs in with its demo credentials.

Run the opt-in live no-seen test only when intentionally validating a configured mailbox. Set `IMAP_AGENT_CLI_LIVE_TEST=1` first:

```text
python -m unittest tests.test_live_no_seen -v
```

Build the package:

```text
uv build --no-sources
```

This project is under active development. The behavior target is documented in [`spec.md`](./spec.md). It is distributed under the [MIT License](./LICENSE).
