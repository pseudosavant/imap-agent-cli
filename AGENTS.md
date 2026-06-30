# Agent Instructions

## Project Purpose

`imap-agent-cli` is a Python CLI for safe agentic email access over generic IMAP. It can search mailboxes, read messages without marking them read, inspect or download attachments when explicitly requested, and append messages to Drafts.

The core safety boundary is strict: never add support for sending email, deleting messages, moving messages, archiving messages, labeling messages, flagging messages, starring messages, marking messages read/unread, or creating/deleting/renaming folders.

## Repository Layout

- `imap_agent_cli.py` is the root local-development wrapper with PEP 723 metadata.
- `src/imap_agent_cli/cli.py` owns argument parsing and command dispatch.
- `src/imap_agent_cli/config.py` owns env-var and profile config behavior.
- `src/imap_agent_cli/imap_client.py` owns IMAP interactions.
- `src/imap_agent_cli/mime.py` owns MIME parsing and draft composition.
- `src/imap_agent_cli/render.py` owns body format rendering and sanitization.
- `src/imap_agent_cli/search.py` owns search criteria construction.
- `src/imap_agent_cli/skill.py` owns the installed `imap` skill text.
- `tests/` contains unit tests and the opt-in local `pymap` integration test.
- `spec.md` is the product behavior target.

## Development Rules

- Keep the PyPI package name, GitHub repository name, and console command aligned as `imap-agent-cli`.
- Keep the root wrapper thin; application logic belongs under `src/imap_agent_cli/`.
- Preserve stable JSON payloads on stdout. Diagnostics, warnings, progress, and errors must go to stderr.
- Do not print secrets, credentials, full message bodies, or attachment contents in logs.
- Keep credentials in environment variables or config references to environment variables. Do not add config examples that store passwords directly.
- Keep README and skill language platform-neutral and agent-tool-neutral. Avoid shell-specific syntax unless explicitly documenting a shell-specific example.
- If changing installed-skill behavior or wording, update `src/imap_agent_cli/skill.py` and `tests/test_skill.py` together.
- If changing MIME parsing, body rendering, draft creation, or search behavior, add focused tests for the contract being changed.

## Safety-Sensitive Implementation Notes

- Reads must avoid changing message state. Preserve no-seen fetch behavior.
- Draft creation is allowed only by appending a new MIME message to the detected or configured Drafts folder.
- Reply drafts should preserve appropriate reply headers such as `In-Reply-To` and `References`.
- Attachment downloads require explicit user/agent intent and an output directory.
- Folder-wide or all-folder operations should remain bounded by defaults and overridable limits.
- HTML email is untrusted input. Sanitize before returning HTML and keep Markdown conversion intentionally lossy but predictable.

## Common Commands

Run the CLI locally:

```text
uv run ./imap_agent_cli.py --help
uv run ./imap_agent_cli.py folders
uv run ./imap_agent_cli.py search --subject invoice
```

Run no-network tests:

```text
python -m unittest discover -v
```

Run the optional local IMAP integration test:

```text
uv run --extra test python -m unittest tests.test_pymap_integration -v
```

Set `IMAP_AGENT_CLI_TEST_PYMAP=1` in the shell before running the integration test. The test starts `pymap dict --demo-data` locally and logs in with demo credentials.

Build the package:

```text
uv build --no-sources
```

## Publishing Notes

- Package metadata lives in `pyproject.toml`.
- The GitHub Actions publish workflow is `.github/workflows/publish.yml`.
- PyPI Trusted Publishing uses:
  - Project: `imap-agent-cli`
  - Owner: `pseudosavant`
  - Repository: `imap-agent-cli`
  - Workflow: `publish.yml`
  - Environment: `pypi`

## Before Finishing Changes

- Run the smallest relevant tests for the change.
- For broad behavior changes, run `python -m unittest discover -v`.
- For packaging or release changes, also run `uv build --no-sources`.
- Check `git status --short` and mention any uncommitted or unverified work.
