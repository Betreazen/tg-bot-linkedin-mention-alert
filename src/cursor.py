"""poll_cursor (SPEC §6): нижняя граница timeRange следующего цикла на каждый org.

Курсор двигается ТОЛЬКО после успешного опроса org — иначе окно перечитается на
следующем цикле (с оверлапом), чтобы не потерять упоминания.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from src.db import storage_errors
from src.timeutil import from_iso, to_iso


def get_cursor(conn: sqlite3.Connection, org_urn: str) -> datetime | None:
    with storage_errors():
        row = conn.execute(
            "SELECT last_polled_at FROM poll_cursor WHERE org_urn = ?", (org_urn,)
        ).fetchone()
    return from_iso(row["last_polled_at"]) if row is not None else None


def set_cursor(conn: sqlite3.Connection, org_urn: str, at: datetime) -> None:
    with storage_errors():
        conn.execute(
            "INSERT INTO poll_cursor (org_urn, last_polled_at) VALUES (?, ?) "
            "ON CONFLICT(org_urn) DO UPDATE SET last_polled_at = excluded.last_polled_at",
            (org_urn, to_iso(at)),
        )
        conn.commit()
