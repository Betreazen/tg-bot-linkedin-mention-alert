"""Единый формат хранения времени: ISO 8601 в UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    return as_utc(dt).isoformat()


def from_iso(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value))
