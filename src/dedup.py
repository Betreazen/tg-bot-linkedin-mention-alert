"""Dedup layer (SPEC §3.2, §5): не отправлять упоминание дважды.

Ключ — `notification_id` (стабильный id LinkedIn либо составной, см. linkedin_client).
Уникальность гарантирована схемой (`UNIQUE`) + `INSERT OR IGNORE`, поэтому дубли не
проходят ни между циклами, ни при рестарте контейнера.

Ретеншен: записи старше RETENTION_DAYS чистятся (`prune`) — окно опроса курсорное и
никогда не смотрит так далеко назад, а таблица не растёт бесконечно.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from src.db import storage_errors
from src.models import Notification
from src.timeutil import to_iso

#: с запасом больше любого окна опроса (курсор + оверлап — минуты, не месяцы)
RETENTION_DAYS = 90


def is_sent(conn: sqlite3.Connection, notification_id: str) -> bool:
    with storage_errors():
        row = conn.execute(
            "SELECT 1 FROM mentions_sent WHERE notification_id = ?", (notification_id,)
        ).fetchone()
    return row is not None


def record_sent(conn: sqlite3.Connection, notification: Notification, sent_at: datetime) -> None:
    with storage_errors():
        conn.execute(
            "INSERT OR IGNORE INTO mentions_sent "
            "(notification_id, org_urn, source_post_urn, detected_at, sent_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                notification.notification_id,
                notification.org_urn,
                notification.activity_urn,
                to_iso(notification.detected_at),
                to_iso(sent_at),
            ),
        )
        conn.commit()


def prune(conn: sqlite3.Connection, now: datetime) -> int:
    """Удалить записи старше RETENTION_DAYS; возвращает число удалённых."""
    threshold = to_iso(now - timedelta(days=RETENTION_DAYS))
    with storage_errors():
        deleted = conn.execute(
            "DELETE FROM mentions_sent WHERE sent_at < ?", (threshold,)
        ).rowcount
        conn.commit()
    return deleted
