from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Defaults:
    profile: str = "default"
    format: str = "json"
    default_folder: str = "INBOX"
    max_results: int = 25
    max_body_chars: int = 12000
    connect_timeout_seconds: int = 15
    read_timeout_seconds: int = 30
    body_format: str = "html"
    include_attachments: bool = False
    exclude_special_folders_from_all: tuple[str, ...] = ("junk", "spam")


@dataclass(frozen=True)
class Profile:
    name: str
    host: str
    port: int = 993
    username: str = ""
    password: str | None = None
    password_env: str = "IMAP_AGENT_CLI_PASSWORD"
    tls: bool = True
    ssl_mode: str = "required"
    drafts_folder: str = ""
    connect_timeout_seconds: int = 15
    read_timeout_seconds: int = 30


@dataclass(frozen=True)
class Config:
    defaults: Defaults = field(default_factory=Defaults)
    profiles: dict[str, Profile] = field(default_factory=dict)
    path: Path | None = None


@dataclass(frozen=True)
class AttachmentInfo:
    part_id: str
    filename: str
    content_type: str
    size_bytes: int | None
    content_id: str | None
    inline: bool
