"""Local TOML storage with ordinary inherited permissions and atomic writes."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

import tomlkit
from filelock import FileLock, Timeout

from .errors import ConfigError


CONFIG_OVERRIDE: ContextVar[Path | None] = ContextVar("config_path", default=None)
CREDENTIALS_OVERRIDE: ContextVar[Path | None] = ContextVar("credentials_path", default=None)


@contextmanager
def paths(config: str | None = None, credentials: str | None = None):
    config_token = CONFIG_OVERRIDE.set(Path(config).expanduser() if config else CONFIG_OVERRIDE.get())
    credentials_token = CREDENTIALS_OVERRIDE.set(Path(credentials).expanduser() if credentials else CREDENTIALS_OVERRIDE.get())
    try:
        yield
    finally:
        CONFIG_OVERRIDE.reset(config_token)
        CREDENTIALS_OVERRIDE.reset(credentials_token)


def read_bytes(path: Path) -> bytes | None:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ConfigError(f"Refusing linked file at {path}. Choose a regular file.")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ConfigError(f"Cannot read {path}. Check the path and file access.") from None


def read_document(path: Path):
    previous = read_bytes(path)
    try:
        document = tomlkit.parse(previous.decode("utf-8")) if previous is not None else tomlkit.document()
    except (ValueError, UnicodeError):
        # Parser errors can contain the line being parsed, including a secret.
        raise ConfigError(f"Invalid TOML at {path}. Repair the file in your terminal. Its contents were not displayed.") from None
    return document, previous


def write_document(path: Path, document, expected: bytes | None) -> bool:
    content = tomlkit.dumps(document).encode("utf-8")
    if content == expected:
        return False
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # mode 0666 follows the process umask and inherited ACLs.
        with FileLock(str(path) + ".lock", timeout=0, mode=0o666):
            if read_bytes(path) != expected:
                raise ConfigError(f"{path} changed during setup. Retry the command.")
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
    except (OSError, Timeout):
        raise ConfigError(f"Cannot save {path}. Check file access or retry after other setup commands finish.") from None
    finally:
        temporary.unlink(missing_ok=True)
    return True
