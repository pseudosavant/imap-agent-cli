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


def _positive_int(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise AppError("invalid_request", f"{name} must be greater than zero.")
    return value


def build_criteria(
    *,
    subject: str | None,
    sender: str | None,
    recipient: str | None,
    message_id: str | None,
    text: str | None,
    since: str | None,
    before: str | None,
    unseen: bool,
    seen: bool,
    answered: bool,
    flagged: bool,
    larger: int | None,
    smaller: int | None,
) -> list[Any]:
    criteria: list[Any] = []
    if subject:
        criteria.extend(["SUBJECT", subject])
    if sender:
        criteria.extend(["FROM", sender])
    if recipient:
        criteria.extend(["TO", recipient])
    if message_id:
        criteria.extend(["HEADER", "Message-ID", message_id])
    if text:
        criteria.extend(["TEXT", text])
    since_date = parse_date(since, "--since")
    before_date = parse_date(before, "--before")
    if since_date:
        criteria.extend(["SINCE", since_date])
    if before_date:
        criteria.extend(["BEFORE", before_date])
    if unseen and seen:
        raise AppError("invalid_request", "--seen and --unseen cannot be combined.")
    if unseen:
        criteria.append("UNSEEN")
    if seen:
        criteria.append("SEEN")
    if answered:
        criteria.append("ANSWERED")
    if flagged:
        criteria.append("FLAGGED")
    larger_value = _positive_int(larger, "--larger")
    smaller_value = _positive_int(smaller, "--smaller")
    if larger_value is not None:
        criteria.extend(["LARGER", larger_value])
    if smaller_value is not None:
        criteria.extend(["SMALLER", smaller_value])
    return criteria or ["ALL"]
