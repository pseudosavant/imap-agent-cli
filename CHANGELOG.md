# Changelog

## 0.4.0 - 2026-10-20

- Initialize standard streams as UTF-8 before output or skill synchronization. Read JSON input as UTF-8 and emit lossless ASCII-escaped JSON for reliable capture and piping on Windows.
- Return structured errors for unexpected exceptions and output failures. Add `--debug` for redacted traceback locations without exception text, source lines, local values, or exception chains.
- Distinguish network timeouts, connection failures, server failures, and missing messages. Include operation, phase, and attempt counts. Retry safe reads once after reconnecting and selecting the folder read-only.
- Keep draft appends single-attempt. Report an unknown outcome after an append network failure so callers can inspect Drafts before creating another message.
- Fetch headers and BODYSTRUCTURE for metadata reads and attachment listings. Fetch only the required text MIME part for body reads and thread context with BODY.PEEK. Read reply source metadata without transferring its attachments.
- Fetch search metadata incrementally for UID ordering and stop after enough qualifying results. Preserve attachment filtering and sort bounded date candidates across folders with timezone-aware comparisons.
- Add `search --minimal`, `--fields`, and `--timings` for smaller responses and optional phase diagnostics.
- Add `--no-skill-sync` and `IMAP_AGENT_CLI_NO_SKILL_SYNC=1` to disable automatic skill checks and writes during ordinary commands and help.
- Document the installed executable as an alternative to uvx in restricted environments. Identify uv cache failures before startup as launcher or environment problems without changing credential locations.
- Update CLI help, the managed skill, and regression coverage for Unicode capture, selective MIME reads, retries, search efficiency, and structured failures.

### Compatibility and limits

- Preserve default response keys. JSON uses ASCII escapes on the wire and preserves exact Unicode values after parsing.
- Attachment `size_bytes` can be `null` when BODYSTRUCTURE cannot provide the exact decoded size. Explicit downloads report exact decoded sizes.
- Search counts describe inspected candidates and observed matches. Date ordering covers the bounded candidate sets across the requested folders.
- `--max-scan` applies per folder after the server search. It does not limit server search work or the UID response. `--max-body-chars` limits returned text after rendering and does not cap transfer of the selected text part.

## 0.2.1 - 2026-09-08

- Direct agents to search only `INBOX` when the user does not specify a folder or broader scope. Do not automatically expand empty or incomplete searches.
- Keep specific-folder, recursive, and all-folder searches available when requested, including through JSON input.
- Clarify search scope in CLI help and documentation. Preserve the existing configurable CLI folder default.
- Add regression coverage for search scope defaults, explicit overrides, and single-folder searches avoiding folder enumeration.

## 0.2.0 - 2026-09-03

- Automatically synchronize existing managed `imap` skills in the standard directory to the running CLI's newer version. No package lookup, uv refresh, or CLI update is performed.
- Store management identity, exact CLI version, and normalized SHA-256 content integrity in `SKILL.md` YAML metadata. Migrate legacy markers and recover missing or malformed versions.
- Preserve modified or unverifiable content with valid versions. Add `uvx imap-agent-cli skill install --force` for explicit replacement of managed content.
- Add read-only `skill status` with JSON and plain output. Keep `install-skill`, `remove-skill`, custom directories, and removal-time `--force`.
- Skip automatic synchronization for local source and editable builds. Custom skills require explicit updates. Updates affect future agent skill loading.
- Replace skills atomically and preserve unrelated files during removal.
