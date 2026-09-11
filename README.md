# imap-agent-cli

`imap-agent-cli` gives coding agents a narrow, safe interface to generic IMAP mailboxes. It can search email, read messages without changing their unread state, inspect or download requested attachments, and save new or reply drafts.

It never sends email and cannot change existing messages or folders.

## Quick start with an agent

You need an IMAP-enabled email account and a compatible password or app password. Setup explains credential creation for Gmail, Fastmail, and iCloud. Other IMAP servers need their provider's hostname and login details. Microsoft 365 and Outlook.com require OAuth and are not supported. Microsoft app passwords are not a workaround.

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) if needed. The managed skill runs the tool with `uvx`. You do not need a global tool installation. uv can obtain a compatible Python automatically. Direct Python installs require Python 3.11 through 3.13. IMAPClient is currently incompatible with Python 3.14.

PowerShell:

```powershell
# Skip this command if uv is already installed.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Open a new PowerShell window after installation.
uv --version
uvx imap-agent-cli setup --format plain
```

Bash on Linux or macOS:

```bash
# Skip these two commands if uv is already installed.
curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"

uv --version
uvx imap-agent-cli setup --format plain
```

Enter your credential only at the masked terminal prompt. Never paste a credential into agent chat. Setup saves account settings and credentials separately, verifies read-only mailbox access, and installs the managed `imap` skill. No separate test command is needed after successful setup.

You can provide information you already know:

```text
uvx imap-agent-cli setup "me@gmail.com"
uvx imap-agent-cli setup "https://app.fastmail.com/"
uvx imap-agent-cli setup --host imap.example.com --username me@example.com
```

A webmail URL is only a provider hint. It is not fetched. Custom email domains require a provider hostname. See [setup and credential guidance](docs/setup.md) for provider requirements and recovery commands.

Start a new agent session if the skill is not available. Then ask:

> Use $imap to list the five newest messages in INBOX. Show senders and subjects only.

The agent must have access to `uvx`, the saved files, and the IMAP server. A remote host or container needs its own runtime and credential provision. If terminal use works but agent use fails, ask the agent to run `uvx imap-agent-cli config check`. Do not ask it to open the credentials file.

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

### Saved accounts

```text
uvx imap-agent-cli setup
uvx imap-agent-cli setup --profile work
uvx imap-agent-cli setup --profile work --set-default
uvx imap-agent-cli profiles
uvx imap-agent-cli config show
```

The first account uses the name `default`. Adding an account preserves the existing default. Pass `--profile work` to mailbox commands to select it.

Non-secret settings live in `~/.imap-agent-cli/config.toml`. Setup saves entered passwords in `~/.imap-agent-cli/credentials.toml` with normal inherited permissions. It does not require a keyring, change ACLs, or persist shell variables. The credential file is plain text. Keep it out of repositories and shared exports.

Repeated setup reuses existing credentials and preserves unrelated configuration and skill content. Use `setup --replace-password` to verify and save a replacement. Unset password environment overrides first. Existing configuration remains usable if verification or saving fails.

### Environment overrides

Environment variables remain available. Connection flags take priority over non-empty environment overrides, followed by the selected saved profile and built-in defaults. Password resolution uses explicit stdin, the profile's named password variable, the global password variable, and finally the matching saved credential. Rejected credentials do not trigger fallback to another source.

PowerShell:

```powershell
$env:IMAP_AGENT_CLI_HOST = "imap.example.com"
$env:IMAP_AGENT_CLI_USERNAME = "me@example.com"
$imapCredential = Read-Host "IMAP password or app password" -AsSecureString
$env:IMAP_AGENT_CLI_PASSWORD = [System.Net.NetworkCredential]::new("", $imapCredential).Password
Remove-Variable imapCredential
uvx imap-agent-cli setup --non-interactive
```

Bash:

```bash
export IMAP_AGENT_CLI_HOST="imap.example.com"
export IMAP_AGENT_CLI_USERNAME="me@example.com"
read -rs -p "IMAP password or app password: " IMAP_AGENT_CLI_PASSWORD
printf '\n'
export IMAP_AGENT_CLI_PASSWORD
uvx imap-agent-cli setup --non-interactive
```

These variables affect the current shell and its children. An already-running agent does not inherit later changes. Setup uses an environment password without saving its value. Secure defaults supply port `993`, `tls=true`, and `ssl_mode=required`.

The existing `config init`, `config add-profile`, and `config set-default-profile` commands remain available. `config init` creates a starter file. Prefer `setup` to complete onboarding. Existing `password_env` references continue to work.

### Check configuration

```text
uvx imap-agent-cli config check
uvx imap-agent-cli config check --profile work --format plain
uvx imap-agent-cli config check --local
```

Checks never write configuration, credentials, or skills. The network check verifies login, opens the default mailbox read-only, and checks bounded metadata access. It never reads bodies or appends a test message. Missing Drafts is a warning for reading. Finding Drafts does not verify append permission or quota. An empty mailbox leaves metadata fetching untested.

Use `--config PATH` or `IMAP_AGENT_CLI_CONFIG` for another configuration file. Use `--credentials-file PATH` or `IMAP_AGENT_CLI_CREDENTIALS_FILE` for a separate credential file. Explicit paths work with setup, diagnostics, and mailbox commands. Relative command-line paths resolve from the working directory. Setup saves an explicitly selected credential file path for later use. Custom skill locations use `--skills-dir PATH`.

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

Search defaults to `INBOX` only, returns metadata only, and limits results to a bounded number. You can override the default folder with `defaults.default_folder` in config. Pass `--folder INBOX` to explicitly select the inbox:

```text
uvx imap-agent-cli search --subject "invoice" --max-results 10
uvx imap-agent-cli search --from "person@example.com" --since 2026-01-01 --max-results 10
uvx imap-agent-cli search --to "me@example.com" --has-attachments --max-results 10 --max-scan 100
```

When requested, search a specific folder, include its children, or search all selectable folders:

```text
uvx imap-agent-cli search --folder "Archive/Support" --subject "contract" --max-results 25
uvx imap-agent-cli search --folder Projects --recursive --subject "contract" --max-results 25
uvx imap-agent-cli search --all-folders --subject "contract" --max-results 25
```

All-folder searches exclude folders marked as Junk or Spam by default. All-folder and recursive searches can be expensive on large nested archives even with a small result limit. The installed skill directs agents to use the inbox unless the user requests another scope. Empty or incomplete results do not authorize automatically expanding the search.

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

Normal mailbox invocations and top-level help, version, and about commands of an installed CLI update a pristine older managed skill to the running CLI version. Synchronization is local. It does not query PyPI, refresh uv's cache, or update the CLI. Missing skills are never installed automatically. Unmanaged, modified, equal-version, and newer skills are preserved.

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

Use `--json -` to read the JSON request from stdin. Operational failures leave stdout empty and write a compact JSON error to stderr. Setup and configuration checks return structured readiness reports when verification or a later step fails. Check the exit status and the `ready` or `ok` field. Human diagnostics go to stderr. Add `--format plain` to setup or config check for a readable report. Logs do not include passwords, message bodies, or attachment content.

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
| `setup` | Verify and save an account, then install the managed skill |
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
