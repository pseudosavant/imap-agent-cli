from __future__ import annotations

from datetime import date
from typing import Any

from .errors import AppError


def parse_date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError("invalid_request", f"{name} must be YYYY-MM-DD.") from exc


def build_criteria(*, subject: str | None, sender: str | None, since: str | None, before: str | None) -> list[Any]:
    criteria: list[Any] = []
    if subject:
        criteria.extend(["SUBJECT", subject])
    if sender:
        criteria.extend(["FROM", sender])
    since_date = parse_date(since, "--since")
    before_date = parse_date(before, "--before")
    if since_date:
        criteria.extend(["SINCE", since_date])
    if before_date:
        criteria.extend(["BEFORE", before_date])
    return criteria or ["ALL"]
