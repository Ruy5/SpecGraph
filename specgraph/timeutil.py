from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip().strip("\"'")
    if text == "" or text.lower() in {"null", "~", "none"}:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dump_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_window(
    moment: datetime,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    """Half-open ``[start, end)``. Missing bounds are open."""
    if start is not None and moment < start:
        return False
    if end is not None and moment >= end:
        return False
    return True
