from __future__ import annotations

import os
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .errors import AppError, ConfigError
from .models import Config, Defaults, Profile
from .credentials import resolve_credential
from .storage import CONFIG_OVERRIDE, read_document, write_document


ENV_PREFIX = "IMAP_AGENT_CLI_"
CONFIG_DIR = Path.home() / ".imap-agent-cli"
CONFIG_PATH = CONFIG_DIR / "config.toml"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ConfigError(f"{name} must be true or false.")
    return normalized in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def _coerce_defaults(data: dict[str, Any]) -> Defaults:
    allowed = set(Defaults.__dataclass_fields__)
    values = {key: value for key, value in data.items() if key in allowed}
    if "exclude_special_folders_from_all" in values:
        values["exclude_special_folders_from_all"] = tuple(values["exclude_special_folders_from_all"])
    return Defaults(**values)


def _coerce_profile(name: str, data: dict[str, Any], defaults: Defaults) -> Profile:
    port = int(data.get("port", 993))
    password_env = str(data.get("password_env") or f"{ENV_PREFIX}PASSWORD")
    password = os.environ.get(password_env) or os.environ.get(f"{ENV_PREFIX}PASSWORD")
    if not isinstance(data.get("tls", True), bool):
        raise ConfigError("Profile tls must be a TOML boolean.")
    return Profile(
        name=name,
        host=str(data.get("host", "")),
        port=port,
        username=str(data.get("username", "")),
        password=password,
        password_env=password_env,
        tls=bool(data.get("tls", True)),
        ssl_mode=str(data.get("ssl_mode", "required")),
        drafts_folder=str(data.get("drafts_folder", "")),
        connect_timeout_seconds=int(data.get("connect_timeout_seconds", defaults.connect_timeout_seconds)),
        read_timeout_seconds=int(data.get("read_timeout_seconds", defaults.read_timeout_seconds)),
        credential_id=str(data.get("credential_id", "")),
        credentials_file=str(data.get("credentials_file", "")),
        credential_source=(f"environment:{password_env}" if os.environ.get(password_env) else
                           "environment:IMAP_AGENT_CLI_PASSWORD" if password else "missing"),
        auth=str(data.get("auth", "password")),
        sender=str(data.get("sender", "")),
    )


def _env_profile() -> Profile | None:
    host = os.environ.get(f"{ENV_PREFIX}HOST")
    if not host:
        return None
    defaults = Defaults(
        max_results=_env_int(f"{ENV_PREFIX}MAX_RESULTS", 25),
        max_scan=_env_int(f"{ENV_PREFIX}MAX_SCAN", 250),
        max_body_chars=_env_int(f"{ENV_PREFIX}MAX_BODY_CHARS", 12000),
        connect_timeout_seconds=_env_int(f"{ENV_PREFIX}CONNECT_TIMEOUT_SECONDS", 15),
        read_timeout_seconds=_env_int(f"{ENV_PREFIX}READ_TIMEOUT_SECONDS", 30),
    )
    return Profile(
        name="default",
        host=host,
        port=_env_int(f"{ENV_PREFIX}PORT", 993),
        username=os.environ.get(f"{ENV_PREFIX}USERNAME", ""),
        password=os.environ.get(f"{ENV_PREFIX}PASSWORD"),
        password_env=f"{ENV_PREFIX}PASSWORD",
        tls=_env_bool(f"{ENV_PREFIX}TLS", True),
        ssl_mode=os.environ.get(f"{ENV_PREFIX}SSL_MODE", "required"),
        drafts_folder=os.environ.get(f"{ENV_PREFIX}DRAFTS_FOLDER", ""),
        connect_timeout_seconds=defaults.connect_timeout_seconds,
        read_timeout_seconds=defaults.read_timeout_seconds,
    )


def config_path(path: Path | None = None) -> Path:
    return Path(path or CONFIG_OVERRIDE.get() or os.environ.get(f"{ENV_PREFIX}CONFIG") or CONFIG_PATH).expanduser()


def _environment(profile: Profile) -> Profile:
    changes: dict[str, Any] = {}
    for field in ("host", "username", "ssl_mode", "drafts_folder"):
        value = os.environ.get(f"{ENV_PREFIX}{field.upper()}")
        if value:
            changes[field] = value
    for field in ("port", "connect_timeout_seconds", "read_timeout_seconds"):
        changes[field] = _env_int(f"{ENV_PREFIX}{field.upper()}", getattr(profile, field))
    changes["tls"] = _env_bool(f"{ENV_PREFIX}TLS", profile.tls)
    if profile.password and profile.credential_source == "missing":
        changes["credential_source"] = "environment:IMAP_AGENT_CLI_PASSWORD"
    return replace(profile, **changes)


def load_config(path: Path | None = None) -> Config:
    path = config_path(path)
    raw, _ = _load_raw(path)
    try:
        defaults = _coerce_defaults(raw["defaults"])
        defaults = replace(defaults, **{
            key: _env_int(f"{ENV_PREFIX}{key.upper()}", getattr(defaults, key))
            for key in ("max_results", "max_scan", "max_body_chars", "connect_timeout_seconds", "read_timeout_seconds")
        })
        if not isinstance(defaults.profile, str) or not isinstance(defaults.default_folder, str):
            raise ValueError
        if any(not isinstance(getattr(defaults, key), int) or getattr(defaults, key) <= 0
               for key in ("max_results", "max_scan", "max_body_chars", "connect_timeout_seconds", "read_timeout_seconds")):
            raise ValueError
        profiles = {}
        for name, data in raw["profiles"].items():
            if not isinstance(data, dict):
                raise ValueError
            profiles[name] = _environment(_coerce_profile(name, data, defaults))
    except (TypeError, ValueError):
        raise ConfigError(f"Invalid configuration values at {path}. Run uvx imap-agent-cli setup in your terminal.") from None
    env_profile = _env_profile()
    if env_profile and "default" not in profiles:
        profiles["default"] = _environment(env_profile)
    return Config(defaults=defaults, profiles=profiles, path=path)


def _load_raw(path: Path):
    raw, previous = read_document(path)
    raw.setdefault("defaults", {})
    raw.setdefault("profiles", {})
    if not isinstance(raw["defaults"], dict) or not isinstance(raw["profiles"], dict):
        raise ConfigError("Config must contain [defaults] and [profiles] tables.")
    return raw, previous


def validate_profile(profile: Profile) -> None:
    from .providers import reject_microsoft
    reject_microsoft(profile.host)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", profile.password_env):
        raise ConfigError("password_env must be an environment variable name using letters, numbers, and underscores.")
    if not profile.host or any(c.isspace() or ord(c) < 32 for c in profile.host) or "://" in profile.host or "@" in profile.host:
        raise ConfigError("An IMAP hostname is required. Run uvx imap-agent-cli setup.")
    if not profile.username or any(ord(c) < 32 for c in profile.username):
        raise ConfigError("An IMAP username is required. Run uvx imap-agent-cli setup.")
    if not 1 <= profile.port <= 65535:
        raise ConfigError("IMAP port must be between 1 and 65535.")
    if profile.ssl_mode not in {"required", "preferred", "disabled"} or not isinstance(profile.tls, bool):
        raise ConfigError("Use ssl_mode required, preferred, or disabled and a boolean tls value.")
    if profile.auth != "password":
        raise ConfigError("Only password and app-password authentication are supported. OAuth is not supported.")
    if profile.connect_timeout_seconds <= 0 or profile.read_timeout_seconds <= 0:
        raise ConfigError("Connection and read timeouts must be positive.")


def resolve_profile(
    config: Config,
    name: str | None,
    *,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    tls: bool | None = None,
    ssl_mode: str | None = None,
) -> Profile:
    profile_name = name or config.defaults.profile
    base = config.profiles.get(profile_name)
    if base is None and not host:
        raise ConfigError(
            f"profile '{profile_name}' was not found. Run 'uvx imap-agent-cli setup' in your terminal."
        )
    if base is None:
        base = _environment(Profile(name=profile_name, host=host or "", username=username or "",
                                    password=os.environ.get(f"{ENV_PREFIX}PASSWORD")))
    values = asdict(base)
    if host is not None:
        values["host"] = host
    if port is not None:
        values["port"] = port
    if username is not None:
        values["username"] = username
    if password is not None:
        values["password"] = password
    if tls is not None:
        values["tls"] = tls
    if ssl_mode is not None:
        values["ssl_mode"] = ssl_mode
    if not values["host"]:
        raise ConfigError(f"profile '{profile_name}' is missing host.")
    if not values["username"]:
        raise ConfigError(f"profile '{profile_name}' is missing username.")
    profile = Profile(**values)
    validate_profile(profile)
    return resolve_credential(profile, config.path or config_path(), password)


def config_status(config: Config) -> dict[str, Any]:
    profiles = []
    for name, base in config.profiles.items():
        error = None
        try:
            profile = resolve_profile(config, name)
        except AppError as exc:
            profile = replace(base, password=None, credential_source="unavailable")
            error = exc.message
        item = {key: value for key, value in asdict(profile).items() if key != "password"}
        item.update(has_password=bool(profile.password), complete=bool(profile.host and profile.username and profile.password))
        if error:
            item["error"] = error
        profiles.append(item)
    return {"path": str(config.path or config_path()), "defaults": asdict(config.defaults), "profiles": profiles}


def init_config(path: Path | None = None, *, from_env: bool = False) -> Path:
    path = config_path(path)
    raw, previous = _load_raw(path)
    if previous is not None:
        return path
    # Keep the legacy starter command. Setup is the onboarding entry point.
    profile = _env_profile() if from_env else None
    profile = profile or Profile(name="default", host="imap.example.com", username="me@example.com")
    save_profile(profile, path=path, expected=previous, defaults=Defaults())
    return path


def save_profile(profile: Profile, *, path: Path, expected: bytes | None,
                 defaults: Defaults | None = None, set_default: bool = False) -> None:
    raw, current = _load_raw(path)
    if current != expected:
        raise ConfigError(f"{path} changed during setup. Retry the command.")
    table = raw["profiles"].setdefault(profile.name, {})
    for key in ("host", "port", "username", "password_env", "tls", "ssl_mode", "drafts_folder",
                "connect_timeout_seconds", "read_timeout_seconds", "credential_id", "credentials_file", "auth", "sender"):
        table[key] = getattr(profile, key)
    if defaults is not None:
        for key, value in asdict(defaults).items():
            if key != "profile":
                raw["defaults"][key] = list(value) if isinstance(value, tuple) else value
    if set_default or "profile" not in raw["defaults"]:
        raw["defaults"]["profile"] = profile.name
    write_document(path, raw, expected)


def set_default_profile(name: str, path: Path | None = None) -> Path:
    path = config_path(path)
    raw, previous = _load_raw(path)
    if name not in raw["profiles"]:
        raise ConfigError(f"Profile '{name}' does not exist. Run uvx imap-agent-cli setup --profile {name}.")
    raw["defaults"]["profile"] = name
    write_document(path, raw, previous)
    return path


def add_profile(name: str, *, host: str, port: int = 993, username: str, password_env: str,
                tls: bool = True, ssl_mode: str = "required", drafts_folder: str = "",
                path: Path | None = None) -> Path:
    path = config_path(path)
    raw, previous = _load_raw(path)
    # Patch only requested connection fields. Keep unknown settings and credential
    # references. Binding checks prevent their reuse if the endpoint changes.
    profile = raw["profiles"].setdefault(name, {})
    profile.update(host=host, port=port, username=username, password_env=password_env,
                   tls=tls, ssl_mode=ssl_mode, drafts_folder=drafts_folder)
    raw["defaults"].setdefault("profile", name)
    write_document(path, raw, previous)
    return path


def remove_profile(name: str, path: Path | None = None) -> Path:
    path = config_path(path)
    raw, previous = _load_raw(path)
    if name not in raw["profiles"]:
        raise ConfigError(f"Profile '{name}' does not exist.")
    del raw["profiles"][name]
    if raw["defaults"].get("profile") == name:
        raw["defaults"]["profile"] = next(iter(raw["profiles"]), "default")
    write_document(path, raw, previous)
    return path
