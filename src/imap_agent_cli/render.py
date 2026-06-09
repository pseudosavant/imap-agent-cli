from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import AppError


def _default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def write_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, default=_default, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def write_error(error: AppError) -> None:
    payload = {
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        }
    }
    sys.stderr.write(json.dumps(payload, ensure_ascii=False))
    sys.stderr.write("\n")
