"""Resolve secrets without exposing them in configuration or diagnostics."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .errors import AppError, ConfigError
from .models import Profile
from .storage import CREDENTIALS_OVERRIDE, read_document, write_document


def credentials_path(config_path: Path, profile: Profile | None = None, *, overrides: bool = True) -> Path:
    explicit = (CREDENTIALS_OVERRIDE.get() or os.environ.get("IMAP_AGENT_CLI_CREDENTIALS_FILE")) if overrides else None
    if explicit:
        return Path(explicit).expanduser().absolute()
    value = explicit or (profile.credentials_file if profile else "")
    if value:
        path = Path(value).expanduser()
        return path if path.is_absolute() else config_path.parent / path
    return config_path.with_name("credentials.toml")


def binding(profile: Profile) -> dict:
    return {"host": profile.host.lower().rstrip("."), "port": profile.port,
            "username": profile.username, "tls": profile.tls, "ssl_mode": profile.ssl_mode,
            "auth": profile.auth}


def resolve_credential(profile: Profile, config_path: Path, password: str | None = None) -> Profile:
    if password is not None:
        return replace(profile, password=password, credential_source="stdin")
    # load_config already resolves environment credentials. Do not parse a file
    # unnecessarily when an explicit credential is available.
    if profile.password:
        return profile
    if not profile.credential_id:
        return profile
    path = credentials_path(config_path, profile)
    try:
        document, _ = read_document(path)
        records = document.get("credentials", {})
        if not isinstance(records, dict):
            raise ConfigError("Invalid credentials table.")
        record = records.get(profile.credential_id)
        if record is None:
            return replace(profile, password=None, credential_source="missing")
        if not isinstance(record, dict) or not isinstance(record.get("password"), str) or not record["password"]:
            raise ConfigError("Invalid credential record.")
    except ConfigError:
        raise AppError("credential_unavailable", f"Cannot read credentials at {path}. Check file access and TOML in your terminal, or supply a password environment override.") from None
    if any(record.get(key) != value for key, value in binding(profile).items()):
        raise AppError("credential_mismatch", "Saved credential does not match this endpoint and username. Run uvx imap-agent-cli setup --replace-password with the same profile and connection flags in your terminal, or supply an explicit password.")
    return replace(profile, password=record["password"], credential_source="file")


def save_credential(profile: Profile, path: Path) -> Profile:
    if not profile.password:
        raise AppError("auth_failed", "No password was entered. Run uvx imap-agent-cli setup in your terminal.")
    document, previous = read_document(path)
    records = document.setdefault("credentials", {})
    if not isinstance(records, dict):
        raise ConfigError(f"Invalid credentials table at {path}. Existing content was preserved.")
    # A fresh immutable record lets the config pointer commit last. A failed
    # config write cannot replace the credential used by an existing profile.
    credential_id = uuid4().hex
    records[credential_id] = {**binding(profile), "password": profile.password}
    write_document(path, document, previous)
    return replace(profile, credential_id=credential_id, credential_source="file")


def remove_credential(credential_id: str, path: Path) -> None:
    """Remove a known staged or superseded record without touching other records."""
    document, previous = read_document(path)
    records = document.get("credentials", {})
    if credential_id in records:
        del records[credential_id]
        write_document(path, document, previous)
