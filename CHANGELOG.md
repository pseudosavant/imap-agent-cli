# Changelog

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
