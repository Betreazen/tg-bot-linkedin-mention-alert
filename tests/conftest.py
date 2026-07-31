from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from src.db import connect, init_db


@pytest.fixture
def valid_env(tmp_path) -> dict[str, str]:
    """Минимальный корректный набор переменных окружения."""
    return {
        "LINKEDIN_CLIENT_ID": "cid",
        "LINKEDIN_CLIENT_SECRET": "secret",
        "LINKEDIN_ORG_URNS": "urn:li:organization:123",
        "TELEGRAM_BOT_TOKEN": "tg-token",
        "TELEGRAM_CHAT_ID": "-100123",
        "HEARTBEAT_PATH": str(tmp_path / "heartbeat"),  # не пишем в системный /tmp
    }


@pytest.fixture
def db_conn() -> Iterator[sqlite3.Connection]:
    """In-memory БД с инициализированной схемой."""
    conn = connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()
