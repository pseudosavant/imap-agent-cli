from __future__ import annotations

import os
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .models import Config, Defaults, Profile


ENV_PREFIX = "IMAP_AGENT_CLI_"
CONFIG_DIR = Path.home() / ".imap-agent-cli"
CONFIG_PATH = CONFIG_DIR / "config.toml"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    if not data.get("host"):
        raise ConfigError(f"profile '{name}' is missing host.")
    port = int(data.get("port", 993))
    password_env = str(data.get("password_env") or f"{ENV_PREFIX}PASSWORD")
    password = os.environ.get(password_env)
    return Profile(
        name=name,
        host=str(data["host"]),
        port=port,
        username=str(data.get("username", "")),
        password=password,
        password_env=password_env,
        tls=bool(data.get("tls", True)),
        ssl_mode=str(data.get("ssl_mode", "required")),
        drafts_folder=str(data.get("drafts_folder", "")),
        connect_timeout_seconds=int(data.get("connect_timeout_seconds", defaults.connect_timeout_seconds)),
        read_timeout_seconds=int(data.get("read_timeout_seconds", defaults.read_timeout_seconds)),
    )


def _env_profile() -> Profile | None:
    host = os.environ.get(f"{ENV_PREFIX}HOST")
    if not host:
        return None
    defaults = Defaults(
        max_results=_env_int(f"{ENV_PREFIX}MAX_RESULTS", 25),
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


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    defaults = Defaults()
    profiles: dict[str, Profile] = {}

    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        defaults = _coerce_defaults(raw.get("defaults", {}))
        raw_profiles = raw.get("profiles", {})
        if not isinstance(raw_profiles, dict):
            raise ConfigError("[profiles] must be a TOML table.")
        profiles = {
            name: _coerce_profile(name, profile_data, defaults)
            for name, profile_data in raw_profiles.items()
            if isinstance(profile_data, dict)
        }

    env_profile = _env_profile()
    if env_profile and "default" not in profiles:
        profiles["default"] = env_profile

    return Config(defaults=defaults, profiles=profiles, path=path)


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"defaults": {}, "profiles": {}}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    raw.setdefault("defaults", {})
    raw.setdefault("profiles", {})
    if not isinstance(raw["defaults"], dict) or not isinstance(raw["profiles"], dict):
        raise ConfigError("config must contain [defaults] and [profiles] tables.")
    return raw


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_raw(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    defaults = raw.get("defaults", {})
    lines.append("[defaults]")
    for key, value in defaults.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    profiles = raw.get("profiles", {})
    for name, profile in profiles.items():
        lines.append(f"[profiles.{name}]")
        for key, value in profile.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
            f"profile '{profile_name}' was not found. Set IMAP_AGENT_CLI_HOST or run 'imap-agent-cli config init'."
        )
    if base is None:
        base = Profile(name=profile_name, host=host or "", username=username or "")
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
    return Profile(**values)


def config_status(config: Config) -> dict[str, Any]:
    return {
        "path": str(config.path or CONFIG_PATH),
        "defaults": asdict(config.defaults),
        "profiles": [
            {
                "name": profile.name,
                "host": profile.host,
                "port": profile.port,
                "username": profile.username,
                "password_env": profile.password_env,
                "has_password": bool(profile.password),
                "tls": profile.tls,
                "ssl_mode": profile.ssl_mode,
                "drafts_folder": profile.drafts_folder,
                "complete": bool(profile.host and profile.username and profile.password),
            }
            for profile in config.profiles.values()
        ],
    }


def init_config(path: Path | None = None, *, from_env: bool = False) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    host = os.environ.get(f"{ENV_PREFIX}HOST", "imap.example.com") if from_env else "imap.example.com"
    port = os.environ.get(f"{ENV_PREFIX}PORT", "993") if from_env else "993"
    username = os.environ.get(f"{ENV_PREFIX}USERNAME", "me@example.com") if from_env else "me@example.com"
    content = f"""[defaults]
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
host = "{host}"
port = {port}
username = "{username}"
password_env = "IMAP_AGENT_CLI_PASSWORD"
tls = true
ssl_mode = "required"
drafts_folder = ""
"""
    path.write_text(content, encoding="utf-8")
    return path


def set_default_profile(name: str, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    raw = _load_raw(path)
    if name not in raw["profiles"]:
        raise ConfigError(f"profile '{name}' does not exist.")
    raw["defaults"]["profile"] = name
    _write_raw(path, raw)
    return path


def add_profile(
    name: str,
    *,
    host: str,
    port: int = 993,
    username: str,
    password_env: str,
    tls: bool = True,
    ssl_mode: str = "required",
    drafts_folder: str = "",
    path: Path | None = None,
) -> Path:
    path = path or CONFIG_PATH
    raw = _load_raw(path)
    raw.setdefault("defaults", {})
    raw.setdefault("profiles", {})
    raw["profiles"][name] = {
        "host": host,
        "port": port,
        "username": username,
        "password_env": password_env,
        "tls": tls,
        "ssl_mode": ssl_mode,
        "drafts_folder": drafts_folder,
    }
    raw["defaults"].setdefault("profile", name)
    raw["defaults"].setdefault("format", "json")
    raw["defaults"].setdefault("default_folder", "INBOX")
    raw["defaults"].setdefault("max_results", 25)
    raw["defaults"].setdefault("max_body_chars", 12000)
    raw["defaults"].setdefault("connect_timeout_seconds", 15)
    raw["defaults"].setdefault("read_timeout_seconds", 30)
    raw["defaults"].setdefault("body_format", "html")
    raw["defaults"].setdefault("include_attachments", False)
    raw["defaults"].setdefault("exclude_special_folders_from_all", ["junk", "spam"])
    _write_raw(path, raw)
    return path


def remove_profile(name: str, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    raw = _load_raw(path)
    if name not in raw["profiles"]:
        raise ConfigError(f"profile '{name}' does not exist.")
    del raw["profiles"][name]
    if raw["defaults"].get("profile") == name:
        raw["defaults"]["profile"] = next(iter(raw["profiles"]), "default")
    _write_raw(path, raw)
    return path
