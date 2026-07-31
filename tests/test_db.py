from __future__ import annotations

import sqlite3

import pytest

from src.db import connect, init_db

EXPECTED_TABLES = {"oauth_token", "mentions_sent", "bot_health", "poll_cursor"}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_init_db_creates_all_tables(db_conn):
    assert EXPECTED_TABLES <= _tables(db_conn)


def test_init_db_is_idempotent(db_conn):
    init_db(db_conn)  # второй вызов не должен падать
    assert EXPECTED_TABLES <= _tables(db_conn)


def test_oauth_token_singleton_check(db_conn):
    insert = (
        "INSERT INTO oauth_token (id, access_token, refresh_token, "
        "access_token_expires_at, refresh_token_expires_at, updated_at) "
        "VALUES (?, 'a', 'r', '2026-01-01', '2027-01-01', '2026-01-01')"
    )
    db_conn.execute(insert, (1,))
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(insert, (2,))


def test_mentions_sent_notification_id_unique(db_conn):
    stmt = (
        "INSERT INTO mentions_sent (notification_id, org_urn, source_post_urn, "
        "detected_at, sent_at) VALUES (?, ?, ?, ?, ?)"
    )
    row = ("n1", "urn:li:organization:1", "urn:li:activity:1", "2026-01-01", "2026-01-01")
    db_conn.execute(stmt, row)
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(stmt, row)


def test_insert_or_ignore_dedup(db_conn):
    stmt = (
        "INSERT OR IGNORE INTO mentions_sent (notification_id, org_urn, "
        "source_post_urn, detected_at, sent_at) VALUES (?, ?, ?, ?, ?)"
    )
    row = ("n1", "urn:li:organization:1", "urn:li:activity:1", "2026-01-01", "2026-01-01")
    db_conn.execute(stmt, row)
    db_conn.execute(stmt, row)  # дубль игнорируется, без исключения
    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM mentions_sent WHERE notification_id='n1'"
    ).fetchone()["c"]
    assert count == 1


def test_connect_creates_parent_dir(tmp_path):
    db_file = tmp_path / "nested" / "dir" / "bot.db"
    conn = connect(str(db_file))
    try:
        init_db(conn)
    finally:
        conn.close()
    assert db_file.exists()
