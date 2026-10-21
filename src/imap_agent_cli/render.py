from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO

from .errors import AppError


def initialize_streams() -> None:
    """Establish UTF-8 before help, synchronization, or command dispatch."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except (OSError, ValueError, TypeError):
                # Embedders may substitute streams or may have already read stdin.
                # JSON output is ASCII escaped and JSON input decodes bytes below.
                pass


def _default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _write(stream: TextIO, text: str) -> None:
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, UnicodeError, ValueError) as exc:
        # Windows can report a closed pipe as EINVAL instead of BrokenPipeError.
        # Prevent a second, unstructured flush error during shutdown.
        try:
            descriptor = stream.fileno()
            if isinstance(descriptor, int):
                with open(os.devnull, "w") as sink:
                    os.dup2(sink.fileno(), descriptor)
        except (AttributeError, OSError, ValueError, TypeError):
            pass
        raise AppError("output_failed", "Could not write the response to the output stream.",
                       operation="output", phase="write") from exc


def write_json(payload: Any) -> None:
    try:
        # ASCII is a UTF-8 subset and works with fixed-encoding host streams.
        # Escaping changes only the wire representation, never parsed values.
        text = json.dumps(payload, default=_default, ensure_ascii=True, indent=2)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AppError("output_failed", "Could not serialize the response as JSON.",
                       operation="output", phase="serialize") from exc
    _write(sys.stdout, text)


def write_text(text: str) -> None:
    _write(sys.stdout, text.rstrip("\n"))


def write_diagnostic(payload: Any) -> None:
    _write(sys.stderr, json.dumps(payload, ensure_ascii=True))


def write_error(error: AppError, *, debug: dict[str, Any] | None = None) -> None:
    detail = {"code": error.code, "message": error.message, "retryable": error.retryable, **error.details}
    if debug is not None:
        detail["debug"] = debug
    write_diagnostic({"error": detail})
