# Spec Draft: `imap-agent-cli` v1

## Purpose

Build a Python CLI named `imap-agent-cli` that lets agentic tools safely inspect email over generic IMAP and create email drafts without any ability to send, delete, move, archive, flag, mark read, or otherwise mutate existing messages.

The tool should follow the same broad product pattern as `sql-agent-cli`:

1. local single-file execution via `uv run ./imap_agent_cli.py ...` using PEP 723 inline script metadata
2. packaged execution via `uvx imap-agent-cli ...`
3. agent-first stdout/stderr behavior with stable JSON output contracts
4. profile-based configuration with a simple env-var path for the common single-account case

Primary audience:

- agentic coding tools such as Codex

Secondary audience:

- developers who need a deterministic local CLI for controlled email inspection and draft creation

License:

- MIT

---

## Product Goals

1. The CLI can search email by subject, sender, date range, folder, and folder scope.
2. The CLI can enumerate folders with useful metadata such as selectable status, delimiter, special-use role, total count, and unread count when available.
3. The CLI can read specific messages by `{profile, folder, uid}` without marking them as read.
4. The CLI can list attachment metadata and download attachments only when explicitly requested.
5. The CLI can create new draft messages by appending MIME messages to the configured or detected Drafts folder.
6. The CLI can create reply drafts from an existing message, including `In-Reply-To` and `References` headers.
7. The CLI must never send email. SMTP is out of scope.
8. The CLI must never delete, move, archive, label, flag, star, mark read, mark unread, or mutate existing messages.
9. Stdout contains payload only. Diagnostics, warnings, progress, and errors go to stderr only.
10. JSON is the only required output format in v1.
11. The project is publishable so `uvx imap-agent-cli --help` works.
12. The repo also keeps a root-level `imap_agent_cli.py` wrapper for `uv run`.

## Non-Goals

1. SMTP send support.
2. Destructive or organizational mailbox actions.
3. OAuth2 setup flows for Gmail or Microsoft 365.
4. Provider-specific APIs such as Gmail API or Microsoft Graph.
5. Interactive TUI behavior.
6. Human-readable default output.
7. Long-term local email indexing in v1.
8. Background polling or notifications.

---

## Naming and Packaging

## Public Command

`imap-agent-cli`

The PyPI package name, GitHub repository name, and CLI command must match: `imap-agent-cli`.

## Recommended Package/Module Layout

```text
imap-agent-cli/
  pyproject.toml
  README.md
  imap_agent_cli.py
  spec.md
  src/
    imap_agent_cli/
      __init__.py
      cli.py
      config.py
      errors.py
      folders.py
      imap_client.py
      messages.py
      mime.py
      models.py
      render.py
      safety.py
      search.py
  tests/
    __init__.py
    test_config.py
    test_search.py
    test_mime.py
    test_render.py
    test_cli.py
```

## Packaging Requirements

1. Publishable Python package via `pyproject.toml`.
2. Console script entry point exposed as `imap-agent-cli`.
3. Python `>=3.11`.
4. Root-level `imap_agent_cli.py` wrapper with PEP 723 metadata for local script execution.
5. The wrapper delegates into the packaged implementation instead of duplicating application logic.

## Dependency Recommendations

Recommended implementation dependencies:

1. `IMAPClient` for IMAP operations.
2. Python standard-library `email` package for parsing and composing MIME messages.
3. `beautifulsoup4` for HTML cleanup and text extraction.
4. `bleach` for sanitized HTML output.
5. `markdownify` for optional HTML-to-Markdown conversion.

Rationale:

1. `IMAPClient` provides a higher-level Pythonic API over stdlib `imaplib`, handles UIDs cleanly, and parses many server responses into useful Python objects.
2. stdlib `email` is sufficient and appropriate for MIME parsing/composition.
3. HTML emails are untrusted content. Sanitization and conversion should be explicit implementation steps, not ad hoc string handling.
4. Markdown conversion is feasible for agent consumption, but it is lossy. The default should preserve sanitized HTML while allowing `--body-format markdown`.

---

## Security and Safety Model

## Allowed IMAP Actions

The tool may use only these behavioral classes:

1. connect/authenticate/logout
2. list folders
3. select or examine folders in read-only mode
4. search for messages
5. fetch message metadata, body parts, and attachments without setting `\Seen`
6. append new messages to the Drafts folder

## Disallowed IMAP Actions

The tool must not expose or internally use behavior that mutates existing messages:

1. `STORE`
2. `COPY`
3. `MOVE`
4. `EXPUNGE`
5. `DELETE`
6. flag/star changes
7. read/unread changes
8. label changes
9. archive operations
10. folder create/delete/rename operations

Implementation requirement:

- Fetches must use peek/no-seen behavior. Reading a message must not mark it as read.

## Draft-Only Write Boundary

The only write operation allowed in v1 is creating a new message in the Drafts folder via IMAP `APPEND`.

Draft creation must:

1. build a valid RFC 5322/MIME message
2. append it to the resolved Drafts folder
3. return metadata about the created draft when available
4. never send the message
5. never save a draft outside the Drafts folder unless the user explicitly configured that folder as the profile's drafts folder

---

## Configuration Model

## Config Path

Use a single user config file at:

```text
~/.imap-agent-cli/config.toml
```

Config-writing commands create the directory and file if they do not exist.

## Single-Profile Env Vars

The simplest single-account setup should work without a config file:

```text
IMAP_AGENT_CLI_HOST=colo-mailin.sona-systems.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=paul@sona-systems.com
IMAP_AGENT_CLI_PASSWORD=...
IMAP_AGENT_CLI_TLS=true
```

Optional env vars:

```text
IMAP_AGENT_CLI_DRAFTS_FOLDER=Drafts
IMAP_AGENT_CLI_CONNECT_TIMEOUT_SECONDS=15
IMAP_AGENT_CLI_READ_TIMEOUT_SECONDS=30
IMAP_AGENT_CLI_MAX_RESULTS=25
IMAP_AGENT_CLI_MAX_BODY_CHARS=12000
```

## Config File Shape

Recommended shape:

```toml
[defaults]
profile = "default"
format = "json"
default_folder = "INBOX"
max_results = 25
max_body_chars = 12000
connect_timeout_seconds = 15
read_timeout_seconds = 30
body_format = "html"
include_attachments = false
exclude_special_folders_from_all = ["junk", "spam"]

[profiles.default]
host = "colo-mailin.sona-systems.com"
port = 993
username = "paul@sona-systems.com"
password_env = "IMAP_AGENT_CLI_PASSWORD"
tls = true
ssl_mode = "required"
drafts_folder = ""

[profiles.personal]
host = "imap.example.com"
port = 993
username = "me@example.com"
password_env = "IMAP_AGENT_CLI_PERSONAL_PASSWORD"
tls = true
ssl_mode = "required"
drafts_folder = "Drafts"
```

Notes:

1. `[defaults].profile` is an alias, not duplicated connection data.
2. Config should store non-secret fields and credential source names.
3. Passwords should come from env vars or stdin, not plaintext config.
4. `drafts_folder = ""` means auto-detect.

## Profile Resolution

Resolution order:

1. explicit CLI flags such as `--host`, `--port`, `--username`, `--tls`, and `--ssl-mode`
2. selected profile from `--profile NAME`
3. default profile from `[defaults].profile`
4. single-profile env vars
5. fail with a clear configuration error

Credential resolution order:

1. `--password-stdin`
2. selected profile's `password_env`
3. single-profile `IMAP_AGENT_CLI_PASSWORD`
4. fail with a clear auth error

## Config Commands

Recommended commands:

```text
imap-agent-cli config init
imap-agent-cli config show
imap-agent-cli config set-default-profile NAME
imap-agent-cli config add-profile NAME --host HOST --port 993 --username USER --password-env ENV_NAME
imap-agent-cli config remove-profile NAME
imap-agent-cli profiles
```

Behavior requirements:

1. `config init` pre-populates `~/.imap-agent-cli/config.toml`.
2. `config init` may accept `--from-env` to seed non-secret fields from `IMAP_AGENT_CLI_*`.
3. `config show` displays effective settings with secrets redacted.
4. `config show` indicates whether each profile is complete enough to connect.
5. Config-writing commands preserve unrelated existing settings where practical.

---

## Provider Presets

Provider presets are named connection templates, not provider-specific behavior layers.

Recommended v1 presets:

1. `generic-ssl-993`: port `993`, TLS enabled, SSL required.
2. `generic-starttls-143`: port `143`, STARTTLS required.
3. `generic-plain-143`: port `143`, TLS disabled, intended only for local test servers.

Possible later presets:

1. `fastmail`
2. `icloud`
3. `yahoo`
4. `outlook-imap`

Preset behavior:

1. Presets fill defaults only.
2. Explicit CLI flags override preset values.
3. Config values override preset values after initialization.
4. Presets must not imply OAuth2 or provider API usage.

Examples:

```text
imap-agent-cli config init --preset generic-ssl-993 --host colo-mailin.sona-systems.com --username paul@sona-systems.com
imap-agent-cli config add-profile work --preset generic-ssl-993 --host colo-mailin.sona-systems.com --username paul@sona-systems.com --password-env IMAP_AGENT_CLI_WORK_PASSWORD
```

---

## Recommended CLI

All commands output JSON to stdout by default. Diagnostics and errors go to stderr.

## Managed Agent Skill

The distribution and command are `imap-agent-cli`. The Python import package is `imap_agent_cli`. The skill is `imap`, installed at `~/.agents/skills/imap/SKILL.md` by `uvx imap-agent-cli skill install`. The original `install-skill` and `remove-skill` commands remain aliases.

`imap_agent_cli.__version__` is the runtime authority and must match `--version`. Canonical skill generation writes `metadata.managed-by: imap-agent-cli`, a quoted `metadata.managed-version`, and a quoted `metadata.managed-content-sha256` with the `sha256:` prefix and 64 lowercase hexadecimal characters. There is no top-level version or sidecar file. The bundled instructions continue to prefer `uvx`.

The content hash covers the entire UTF-8 file after normalizing CRLF and CR to LF and replacing only the hash field's value with `""`. Verification uses the installed text without reserializing YAML. Generated files have LF endings, a trailing newline, and no byte order mark. This detects modifications and is not a signature.

Ordinary invocations, including help, version, about, and no arguments, check only an already-installed skill in the standard location. Skill-management commands skip this check. Compare versions with PEP 440. Replace pristine older managed content. Preserve equal or newer versions and unmanaged content. Preserve older content with a missing, malformed, or mismatched hash and recommend `uvx imap-agent-cli skill install --force`.

The legacy HTML marker remains recognized unless front matter assigns ownership to another tool. Legacy content without a version migrates as version 0. Missing or malformed managed versions intentionally recover through replacement without integrity verification. Newly generated metadata becomes authoritative.

`skill install` creates missing skills and updates pristine older managed skills. `--force` permits replacement of altered managed content, including at the same version. It never overwrites unmanaged content or downgrades a newer version. `skill remove` manages only `SKILL.md`, keeps unrelated files, and retains its explicit force override for unmanaged files. Unexpected directories and linked skill paths are refused.

`skill status` is read-only. JSON is the default, with `--format plain` available. Report path, installation and ownership, running and installed versions, version relationship, integrity, automatic eligibility, local-development exclusion, and any force recommendation. All skill commands accept `--skills-dir PATH`. Custom locations require explicit updates.

Automatic synchronization excludes local checkouts, local source installations, editable builds, and unverified installation origins. Use installed-distribution file records and PEP 610 metadata. Built wheel installations remain eligible. Explicit skill commands work from development builds.

Synchronization performs no package-index lookup, uv cache refresh, or CLI update. Replacement uses a flushed and closed temporary file in the target directory and an atomic replace after rechecking the installed bytes. Concurrent changes abort that attempt. Failures do not change the primary command's exit status. Notices go only to stderr. Updates apply to future skill loading and may not change instructions already loaded in an agent session.

## Folder Commands

List folders:

```text
imap-agent-cli folders
imap-agent-cli folders --profile work
```

Example output:

```json
{
  "profile": "default",
  "folders": [
    {
      "name": "INBOX",
      "delimiter": "/",
      "selectable": true,
      "special_use": "inbox",
      "total": 42,
      "unread": 3,
      "children": []
    }
  ]
}
```

Folder metadata should include provider flags and special-use roles where the server exposes them.

## Search Command

Primary search shape:

```text
imap-agent-cli search --subject "invoice"
imap-agent-cli search --from "person@example.com"
imap-agent-cli search --since 2026-01-01 --before 2026-02-01
imap-agent-cli search --folder INBOX --subject "build failed"
imap-agent-cli search --folder Projects --recursive --from "person@example.com"
imap-agent-cli search --all-folders --subject "contract"
```

JSON query input:

```text
imap-agent-cli search --json query.json
Get-Content query.json | imap-agent-cli search --json -
```

Recommended JSON query shape:

```json
{
  "profile": "default",
  "folder": "INBOX",
  "scope": "folder",
  "subject": "invoice",
  "from": "person@example.com",
  "since": "2026-01-01",
  "before": "2026-02-01",
  "max_results": 25,
  "include_body": false,
  "include_attachments": false
}
```

Folder scope values:

1. `folder`: selected folder only
2. `recursive`: selected folder and child folders
3. `all`: all selectable folders except Junk/Spam by default

Default search behavior:

1. default folder is `INBOX`
2. default scope is `folder`
3. max results default to `25`
4. results sort newest first where practical
5. all-folder search excludes folders with special-use `junk` or `spam`
6. all-folder search may include Trash, Archive, Sent, and Drafts

Example search output:

```json
{
  "profile": "default",
  "query": {
    "folder": "INBOX",
    "scope": "folder",
    "subject": "invoice",
    "max_results": 25
  },
  "results": [
    {
      "folder": "INBOX",
      "uid": 12345,
      "message_id": "<abc@example.com>",
      "date": "2026-01-15T20:33:10Z",
      "from": [{"name": "Example Sender", "email": "sender@example.com"}],
      "to": [{"name": "Paul", "email": "paul@example.com"}],
      "cc": [],
      "subject": "Invoice for January",
      "has_attachments": true,
      "attachment_count": 1,
      "size_bytes": 48291
    }
  ],
  "truncated": false
}
```

## Read Command

Read by folder and UID:

```text
imap-agent-cli read --folder INBOX --uid 12345
imap-agent-cli read --folder INBOX --uid 12345 --body-format markdown
imap-agent-cli read --folder INBOX --uid 12345 --body-format raw-html
imap-agent-cli read --folder INBOX --uid 12345 --include-attachments
```

JSON input:

```text
imap-agent-cli read --json read.json
Get-Content read.json | imap-agent-cli read --json -
```

Body format values:

1. `html`: sanitized HTML if an HTML body exists, otherwise plain text
2. `markdown`: Markdown converted from HTML if HTML exists, otherwise plain text
3. `plain`: plain text body where available, otherwise text extracted from HTML
4. `raw-html`: unsanitized HTML, explicitly requested only
5. `metadata`: no body

Default body behavior:

1. `body_format = "html"`
2. HTML output must be sanitized by default.
3. If no HTML body exists, return the plain text body.
4. `max_body_chars` defaults to `12000`.
5. Body truncation must be reported in JSON.
6. Attachment content is never returned inline.

Sanitized HTML policy:

1. remove scripts, event handlers, forms, iframes, objects, embedded active content, and unsafe URLs
2. remove or neutralize remote resource references such as tracking pixels
3. preserve basic formatting, links, tables, block quotes, and lists where practical
4. include a `sanitized: true` field in the response

Markdown policy:

1. Markdown conversion should use a maintained parser/converter such as `markdownify`.
2. Markdown conversion is lossy and optional.
3. Markdown should be useful for agent reasoning, not a pixel-perfect rendering of the original email.

Example read output:

```json
{
  "profile": "default",
  "folder": "INBOX",
  "uid": 12345,
  "message_id": "<abc@example.com>",
  "date": "2026-01-15T20:33:10Z",
  "headers": {
    "subject": "Invoice for January",
    "from": [{"name": "Example Sender", "email": "sender@example.com"}],
    "to": [{"name": "Paul", "email": "paul@example.com"}],
    "cc": [],
    "reply_to": []
  },
  "body": {
    "format": "html",
    "content": "<p>Hello...</p>",
    "sanitized": true,
    "truncated": false,
    "max_chars": 12000
  },
  "attachments": [
    {
      "part_id": "2",
      "filename": "invoice.pdf",
      "content_type": "application/pdf",
      "size_bytes": 39122,
      "content_id": null,
      "inline": false
    }
  ]
}
```

## Attachment Commands

Attachment download requires an explicit destination:

```text
imap-agent-cli attachments --folder INBOX --uid 12345
imap-agent-cli attachments download --folder INBOX --uid 12345 --part-id 2 --output-dir C:\tmp\email-attachments
imap-agent-cli attachments download --folder INBOX --uid 12345 --all --output-dir C:\tmp\email-attachments
```

Requirements:

1. Attachment metadata can be listed without downloading content.
2. Downloads require `--output-dir`.
3. Filenames must be sanitized to avoid path traversal.
4. Existing files must not be overwritten unless `--overwrite` is provided.
5. Output JSON includes absolute saved paths, content types, sizes, and hashes.
6. Inline attachments are excluded by default unless `--include-inline` is provided.

## Draft Command

Create a new draft:

```text
imap-agent-cli draft create --to person@example.com --subject "Hello" --body-file body.html --body-format html
imap-agent-cli draft create --json draft.json
Get-Content draft.json | imap-agent-cli draft create --json -
```

Recommended JSON input:

```json
{
  "profile": "default",
  "to": [{"email": "person@example.com", "name": "Person"}],
  "cc": [],
  "bcc": [],
  "subject": "Hello",
  "body": "<p>Hello from Codex.</p>",
  "body_format": "html",
  "attachments": [
    "C:/tmp/report.pdf"
  ]
}
```

Create a reply draft:

```text
imap-agent-cli draft reply --folder INBOX --uid 12345 --body-file reply.html --body-format html
imap-agent-cli draft reply --folder INBOX --uid 12345 --to override@example.com --body-file reply.md --body-format markdown
imap-agent-cli draft reply --json reply.json
```

Recommended reply JSON input:

```json
{
  "profile": "default",
  "source": {
    "folder": "INBOX",
    "uid": 12345
  },
  "body": "<p>Thanks. I will take a look.</p>",
  "body_format": "html",
  "include_quoted_original": false,
  "attachments": []
}
```

Reply draft behavior:

1. Read source message using no-seen fetch behavior.
2. Default `To` from `Reply-To` when present, otherwise `From`.
3. Default `Subject` to `Re: <original subject>` unless it already begins with a reply prefix.
4. Populate `In-Reply-To` from source `Message-ID`.
5. Populate `References` from source `References` plus source `Message-ID`.
6. Allow explicit `--to`, `--cc`, `--bcc`, and `--subject` overrides.
7. Do not include quoted original by default.

Draft output:

```json
{
  "profile": "default",
  "drafts_folder": "Drafts",
  "created": true,
  "append_uid": 67890,
  "message_id": "<generated@example.local>",
  "subject": "Re: Invoice for January",
  "to": [{"name": "Example Sender", "email": "sender@example.com"}],
  "attachment_count": 0
}
```

## Draft Folder Detection

Draft folder resolution order:

1. explicit CLI `--drafts-folder`
2. selected profile `drafts_folder`
3. folder with IMAP special-use `\Drafts`
4. common folder names, in order: `Drafts`, `INBOX.Drafts`, `[Gmail]/Drafts`
5. fail with a clear error that asks the user to configure `drafts_folder`

The selected Drafts folder should be cached only in config when the user explicitly asks the tool to write it.

---

## Output and Errors

## Stdout/Stderr Contract

1. Stdout is JSON payload only.
2. Stderr is for diagnostics, warnings, progress, and errors.
3. Logs must not include passwords or message bodies.
4. Subjects, senders, recipients, and folder names may appear in payload JSON and should not be duplicated in logs by default.

## Error Shape

On failure, stdout should be empty and stderr should contain a compact JSON error by default:

```json
{
  "error": {
    "code": "auth_failed",
    "message": "IMAP authentication failed for profile 'default'.",
    "retryable": false
  }
}
```

Recommended error codes:

1. `config_missing`
2. `config_invalid`
3. `auth_failed`
4. `connection_failed`
5. `folder_not_found`
6. `message_not_found`
7. `drafts_folder_not_found`
8. `invalid_request`
9. `attachment_not_found`
10. `write_not_allowed`
11. `server_error`

## Logging

Recommended logging flags:

```text
imap-agent-cli search --subject test --verbose
imap-agent-cli search --subject test --log-file C:\tmp\imap-agent-cli.log
```

Requirements:

1. Default logs go to stderr only.
2. `--log-file` writes diagnostics to the specified file.
3. Log files must redact secrets.
4. Message bodies and attachment content must not be logged.
5. Debug logging may include IMAP command names but must not include raw credentials.

---

## Limits and Guardrails

Default limits:

1. `max_results = 25`
2. `max_body_chars = 12000`
3. `connect_timeout_seconds = 15`
4. `read_timeout_seconds = 30`
5. attachment metadata included only when requested
6. attachment content downloaded only by explicit command
7. raw HTML and raw source disabled unless explicitly requested

Override examples:

```text
imap-agent-cli search --subject invoice --max-results 100
imap-agent-cli read --folder INBOX --uid 12345 --max-body-chars 50000
imap-agent-cli read --folder INBOX --uid 12345 --body-format raw-html
```

Hard safety requirements:

1. The CLI must not provide a `send` command.
2. The CLI must not expose a generic "run IMAP command" escape hatch.
3. The CLI must not provide flags that modify existing message flags.
4. The CLI must not auto-download attachments while reading/searching.

---

## Testing Strategy

## Unit Tests

Unit tests should cover:

1. config/env/profile resolution
2. password source resolution
3. folder scope expansion rules
4. search criteria construction
5. MIME parsing
6. sanitized HTML output
7. Markdown conversion
8. attachment metadata extraction
9. attachment filename sanitization
10. draft MIME generation
11. reply header generation
12. JSON output contracts
13. no-seen fetch behavior at the adapter boundary

## Local Integration Tests

Use `pymap` as the preferred repeatable local IMAP server for integration tests.

Recommended local test target:

```text
uv run pymap --port 1143 --debug maildir .tmp/imap-maildir
```

`pymap` is useful because it is a lightweight Python IMAP server with pluggable backends such as Maildir. Tests can seed a temporary Maildir or use `pymap` administrative helpers, then verify `imap-agent-cli` behavior against IMAP without Docker or real work credentials.

Local test requirements:

1. Tests must not require the user's real IMAP account.
2. Local integration tests should run on Windows without Docker.
3. Live provider tests must be gated behind explicit env vars such as `IMAP_AGENT_CLI_TEST_LIVE=1`.
4. Live provider tests must not create or mutate anything except test drafts in the configured Drafts folder.
5. The default test suite should run without network access where practical.

## Manual Smoke Test

Against the user's current generic IMAP settings:

```text
IMAP_AGENT_CLI_HOST=colo-mailin.sona-systems.com
IMAP_AGENT_CLI_PORT=993
IMAP_AGENT_CLI_USERNAME=paul@sona-systems.com
IMAP_AGENT_CLI_PASSWORD=...
IMAP_AGENT_CLI_TLS=true

uv run ./imap_agent_cli.py folders
uv run ./imap_agent_cli.py search --subject test --max-results 5
uv run ./imap_agent_cli.py read --folder INBOX --uid <uid>
uv run ./imap_agent_cli.py draft create --to paul@sona-systems.com --subject "Draft smoke test" --body "This is a draft only."
```

---

## Documentation Requirements

README should include:

1. clear warning that the tool can create drafts but cannot send email
2. quick start with env vars
3. config file example with default and named profiles
4. examples for folders, search, read, attachment download, draft create, and draft reply
5. explanation of sanitized HTML vs Markdown vs raw HTML
6. explanation of no-seen read behavior
7. local `pymap` test instructions
8. troubleshooting for Drafts folder detection

---

## Open Decisions

1. Whether v1 should support STARTTLS on port 143 or defer it until after SSL-on-993 works.
2. Whether to expose `raw-source` output for full RFC 822 messages, and if so under what flag name.
3. Whether draft creation should support inline images in v1 or only normal file attachments.
4. Whether to add a `--since-days N` convenience filter in addition to explicit date ranges.
