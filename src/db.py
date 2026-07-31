"""SQLite: соединение и идемпотентная инициализация схемы (SPEC §6).

Ошибки SQLite оборачиваются в StorageError (см. storage_errors) — так сбои хранилища
проходят через тот же учёт/алертинг, что и сетевые, а не мимо него.
Файл БД и его каталог ужимаются до 0600/0700: в БД лежат OAuth-токены в открытом виде.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from src.errors import StorageError

MEMORY_PATH = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_token (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    access_token_expires_at TIMESTAMP NOT NULL,
    refresh_token_expires_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS mentions_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT UNIQUE NOT NULL,
    org_urn TEXT NOT NULL,
    source_post_urn TEXT NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    sent_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_health (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_successful_poll TIMESTAMP,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_rate_limited INTEGER NOT NULL DEFAULT 0,
    last_cycle_at TIMESTAMP,
    last_service_alert_at TIMESTAMP,
    last_service_alert_kind TEXT,
    last_refresh_warning_date TEXT
);

CREATE TABLE IF NOT EXISTS poll_cursor (
    org_urn TEXT PRIMARY KEY,
    last_polled_at TIMESTAMP NOT NULL
);
"""

#: колонки, добавленные после v1 — накатываются на существующие БД идемпотентно
_MIGRATIONS = (
    "ALTER TABLE bot_health ADD COLUMN consecutive_rate_limited INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE bot_health ADD COLUMN last_cycle_at TIMESTAMP",
    "ALTER TABLE bot_health ADD COLUMN last_service_alert_at TIMESTAMP",
    "ALTER TABLE bot_health ADD COLUMN last_service_alert_kind TEXT",
    "ALTER TABLE bot_health ADD COLUMN last_refresh_warning_date TEXT",
)


@contextmanager
def storage_errors() -> Iterator[None]:
    """sqlite3.Error → StorageError: сбой БД идёт в общий учёт сбоев, а не мимо."""
    try:
        yield
    except sqlite3.Error as exc:
        raise StorageError(f"SQLite: {type(exc).__name__}: {exc}") from exc


def connect(db_path: str) -> sqlite3.Connection:
    """Открыть соединение; для файловой БД — создать каталог и ужать права."""
    with storage_errors():
        if db_path != MEMORY_PATH:
            path = Path(db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    if db_path != MEMORY_PATH:
        # в БД токены в открытом виде — никому, кроме владельца (best effort на Windows)
        with suppress(OSError):
            os.chmod(Path(db_path).expanduser(), 0o600)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Создать таблицы и накатить миграции колонок. Идемпотентно."""
    with storage_errors():
        conn.executescript(_SCHEMA)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.commit()
