# Setup and credentials

Run `uvx imap-agent-cli setup` in your own terminal. The tool prompts only for missing settings and credentials. Password input is masked with asterisks. It never requests a secret in agent chat.

## Provider requirements

| Provider | Credential and creation steps | Renewal |
| --- | --- | --- |
| Generic IMAP | Ask your provider for its IMAP hostname, login identity, and authentication requirements. Prefer an app password when offered. An administrator may need to enable IMAP. | Follow the provider's expiration and revocation policy. There is no universal IMAP credential lifetime. |
| Gmail | Enable 2-Step Verification. Open [Google App Passwords](https://myaccount.google.com/apppasswords) and create one named imap-agent-cli. The server is `imap.gmail.com:993`. | Google revokes app passwords when the account password changes. Generate a replacement. There is no refresh-token flow or configurable expiry in this tool. |
| Google Workspace | App passwords are usable only when account and organization policy permit them. Administrators control IMAP access and allowed clients. Do not enable legacy less-secure-app access. | Administrator policy or account changes may require a new credential or prevent this authentication method entirely. |
| Fastmail | Open Settings, Privacy & Security, then Connected apps & API tokens. Create an app password with mail-only access. Use `imap.fastmail.com:993` and your Fastmail login address. Your plan must support third-party IMAP clients. | Replace the credential if disabled or removed. The reviewed guidance gives no fixed expiration. Ordinary Fastmail passwords do not work. |
| iCloud | Enable two-factor authentication. At [Apple Account](https://account.apple.com/), open Sign-In and Security, then App-Specific Passwords. Generate a password named imap-agent-cli. Use `imap.mail.me.com:993`. | Changing or resetting the primary Apple Account password revokes app-specific passwords. Generate a replacement. |
| Microsoft 365 and Outlook.com | Unsupported. These services require OAuth. The tool does not register a Microsoft application or implement OAuth. Microsoft app passwords are not a workaround. | Not applicable. |

Google recommends OAuth when supported. App passwords may be unavailable for Advanced Protection, security-key-only 2-Step Verification, or organization accounts. Personal Gmail always enables IMAP. Workspace administrators control it separately. Apple documents both short and full-address IMAP usernames. Use `--username` to correct a login identity and `--sender` when the draft sender address differs from it.

Official requirements:

- [Google app passwords](https://support.google.com/accounts/answer/185833?hl=en)
- [Gmail client access](https://support.google.com/mail/answer/7126229?hl=en)
- [Google IMAP endpoint](https://developers.google.com/workspace/gmail/imap/imap-smtp)
- [Workspace app-password exception](https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth)
- [Workspace administrator controls](https://knowledge.workspace.google.com/admin/sync/turn-pop-and-imap-on-or-off-for-users)
- [Fastmail app passwords](https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords)
- [Fastmail server settings](https://www.fastmail.help/hc/en-us/articles/1500000278342-Server-names-and-ports)
- [Apple app-specific passwords](https://support.apple.com/en-us/102654)
- [iCloud server settings](https://support.apple.com/en-us/102525)
- [Exchange Online authentication requirements](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
- [Outlook.com IMAP requirements](https://support.microsoft.com/en-US/Outlook/pop-imap-and-smtp-settings-for-outlook-com)

Provider presets are connection hints, not a claim that a particular account was tested. Custom domains do not identify their IMAP server reliably. Setup never sends credentials to a guessed server or fetches a webmail URL.

## Permissions and verification

Reading needs mailbox lookup and read access. On servers implementing standard IMAP ACLs, these are `l` and `r`. Draft append needs `i` on the selected Drafts folder. The current draft command does not request flags during append. The CLI needs no SMTP sending permission, deletion permission, or folder-management permission. See [IMAP ACL rights](https://www.rfc-editor.org/rfc/rfc4314.html#section-2.1).

Provider app passwords may grant broader access, including writes and sending through another program. The CLI enforces its own restrictions. The credential itself is not necessarily read-only.

Setup and `config check` authenticate, inspect capabilities and folder names, open the default mailbox with `EXAMINE`, and inspect at most one message's metadata. The search covers at most 250 possible UIDs. No message body or attachment is fetched. No draft is appended. An empty mailbox or unavailable UIDNEXT leaves metadata fetching untested. Missing or ambiguous Drafts is advisory for reading.

Verification uses configured connection and read timeouts, a 60-second operation budget after sign-in, and a limit of 1000 returned folders. These are socket timeouts, not a hard wall-clock limit on DNS resolution or a server that sends a slow continuous response. The IMAP library materializes the LIST response before the folder count is checked. This is not a streaming response-size limit.

`EXAMINE` avoids changing permanent mailbox state, including `\Recent`. Fetches in ordinary read commands use `BODY.PEEK`. There is no read-write selection fallback. See [IMAP read-only selection](https://www.rfc-editor.org/rfc/rfc3501.html#section-6.3.2).

Success establishes only the tested operations from the current execution environment. It does not establish draft append permission, quota, message-body rendering, attachment download, access to every folder, or future credential validity. Sign-in can create provider audit records or security notifications.

## Storage and precedence

Ordinary settings live in `~/.imap-agent-cli/config.toml`. Entered credentials live in the separate `credentials.toml`. Profile records contain credential references, never passwords. Saved credentials are bound to the hostname, port, username, transport settings, and authentication method. An endpoint override cannot silently reuse a saved credential for another endpoint.

Files use normal inherited ACLs and the process umask. The tool does not require a keyring, change permissions, or encrypt these files. Keep them outside repositories and shared exports. A stolen credential may grant more access than the CLI exposes.

Connection settings resolve from flags or the supplied setup target, non-empty environment overrides, the selected saved profile, then built-in defaults. This makes environment settings explicit overrides, including when a saved default profile exists. Password resolution is:

1. Explicit `--password-stdin` input.
2. The selected profile's `password_env` variable.
3. `IMAP_AGENT_CLI_PASSWORD`.
4. A matching saved credential.

Environment passwords are never automatically saved. A rejected credential does not trigger another source. A valid environment or stdin credential can bypass an unavailable credentials file. Password characters are preserved, including whitespace.

Use `--config PATH` or `IMAP_AGENT_CLI_CONFIG` for a different configuration. Use `--credentials-file PATH` or `IMAP_AGENT_CLI_CREDENTIALS_FILE` for credentials. Explicit path flags override path variables. Relative CLI paths are relative to the working directory. A relative credential path saved in a profile is relative to the configuration directory. An explicit setup `--credentials-file` is saved as an absolute profile reference. A path environment override must also be present in later executions.

Replacement verifies before saving. The credential record is staged first, then the profile pointer commits atomically. A failed configuration write cannot replace the credential used by the old profile. Superseded records in the same file that are unreferenced by other profiles in the same configuration are removed after commit. Switching credential files preserves the old file. Use a separate credentials file for each independent configuration file. Do not share internal credential identifiers across independent configurations.

File locks coordinate simultaneous CLI writers. Concurrent changes detected before replacement abort the write. Lock files may remain alongside the TOML files. They contain no credentials. Configuration updates preserve unrelated tables, comments, and profiles. An external editor does not participate in the CLI's advisory locking.

## Repair setup

Inspect without modifying local files:

```text
uvx imap-agent-cli config check --profile work --format plain
uvx imap-agent-cli config check --profile work --local
```

Replace a saved password in your terminal:

```text
uvx imap-agent-cli setup --profile work --replace-password
```

If an environment override is active, unset it first. The diagnostic identifies the exact variable. For the global variable:

PowerShell:

```powershell
Remove-Item Env:IMAP_AGENT_CLI_PASSWORD -ErrorAction SilentlyContinue
uvx imap-agent-cli setup --profile work --replace-password
```

Bash:

```bash
unset IMAP_AGENT_CLI_PASSWORD
uvx imap-agent-cli setup --profile work --replace-password
```

Correct a connection or folder:

```text
uvx imap-agent-cli setup --profile work --host imap.example.com
uvx imap-agent-cli setup --profile work --default-folder INBOX
uvx imap-agent-cli setup --profile work --drafts-folder "Drafts"
```

Changing the endpoint with a saved credential requires an explicit replacement credential. TLS certificate errors need a corrected hostname, trust configuration, or network route. Do not disable encryption as a repair.

Inspect a protected skill before deciding whether to replace it:

```text
uvx imap-agent-cli skill status --format plain
uvx imap-agent-cli skill install
```

Setup never forces a skill replacement. Modified and unmanaged skills are preserved. Explicit `skill install --force` still refuses unmanaged content and newer versions. Use `--skills-dir PATH` consistently for custom skill roots.

## Agent execution

Mailbox commands never prompt. `setup --non-interactive` fails without writing account settings when required input is missing. It uses provisioned environment or file credentials. Explicit `--password-stdin` can accept a credential from a trusted secret source and saves it during setup. Never put a literal credential in a shell command. Do not combine credential stdin with JSON stdin.

Setup does not persist shell variables. An already-running desktop agent does not receive environment changes from another terminal. Sandboxes need access to the selected files and IMAP network endpoint. Remote hosts and containers need their own runtime and credentials, preferably a separate app password per host. Do not automatically copy secrets between hosts. The CLI does not infer that terminal permissions apply to an agent.

A successful CLI check does not prove that the harness loaded the installed skill. Start a new agent session if needed. The agent should use diagnostics rather than open credential files. A missing skill is reported separately from mailbox readiness.

Initial uv package resolution needs access to package and Python downloads. Offline execution requires a provisioned runtime or a usable cache. The managed skill does not automatically update the CLI.
