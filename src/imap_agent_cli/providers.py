"""Conservative provider hints. Never discover a credential destination by HTTP."""
from __future__ import annotations

from urllib.parse import urlsplit

from .errors import AppError


PROVIDERS = {
    "gmail": ("imap.gmail.com", "Google", "https://myaccount.google.com/apppasswords",
              "Turn on 2-Step Verification, then create an app password named imap-agent-cli.\n"
              "App passwords may be unavailable due to account or administrator policy.\n"
              "Changing your Google password revokes app passwords. Create a replacement if revoked."),
    "fastmail": ("imap.fastmail.com", "Fastmail", "https://app.fastmail.com/settings/security",
                 "Open Connected apps & API tokens, then create an app password. Choose mail-only access.\n"
                 "Your plan must support IMAP. Your ordinary Fastmail password will not work.\n"
                 "No fixed expiry is documented. Replace the app password if disabled or removed."),
    "icloud": ("imap.mail.me.com", "iCloud", "https://account.apple.com/",
               "Enable two-factor authentication. Open Sign-In and Security, then App-Specific Passwords.\n"
               "Generate a password named imap-agent-cli.\n"
               "Changing or resetting your Apple Account password revokes app-specific passwords."),
}
DOMAINS = {"gmail.com": "gmail", "googlemail.com": "gmail", "fastmail.com": "fastmail",
           "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud"}
WEBMAIL = {"mail.google.com": "gmail", "app.fastmail.com": "fastmail", "www.icloud.com": "icloud", "icloud.com": "icloud"}
MICROSOFT = {"outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.office365.com",
             "outlook.office.com", "outlook.live.com", "imap-mail.outlook.com"}


def provider_for(host: str) -> str | None:
    return next((name for name, info in PROVIDERS.items() if info[0] == host.lower().rstrip(".")), None)


def reject_microsoft(host: str) -> None:
    if host.lower().rstrip(".") in MICROSOFT:
        raise AppError("auth_unsupported", "Microsoft 365 and Outlook.com require OAuth. This tool supports passwords and app passwords only. Microsoft app passwords are not a workaround. Run uvx imap-agent-cli setup --help for supported account types.")


def parse_target(value: str) -> dict:
    value = value.strip()
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise AppError("invalid_request", "Enter an email address, IMAP hostname, or supported webmail URL.")
    if "://" in value:
        try:
            url = urlsplit(value)
            if not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
                raise ValueError
            reject_microsoft(url.hostname)
            if url.scheme == "https" and url.hostname in WEBMAIL and url.port in (None, 443):
                return {"host": PROVIDERS[WEBMAIL[url.hostname]][0]}
            if url.scheme in {"imap", "imaps"} and url.path in ("", "/"):
                return {"host": url.hostname, "port": url.port or (993 if url.scheme == "imaps" else 143),
                        "tls": url.scheme == "imaps", "ssl_mode": "required"}
        except ValueError:
            pass
        raise AppError("invalid_request", "Use a supported webmail URL or imaps://hostname. URLs must not contain credentials, query strings, or fragments.")
    if "@" in value:
        if value.count("@") != 1 or not all(value.split("@")):
            raise AppError("invalid_request", "Enter a complete email address.")
        domain = value.rsplit("@", 1)[1].lower()
        reject_microsoft(domain)
        result = {"username": value, "sender": value}
        if domain in DOMAINS:
            result["host"] = PROVIDERS[DOMAINS[domain]][0]
        return result
    reject_microsoft(value)
    return {"host": value}
