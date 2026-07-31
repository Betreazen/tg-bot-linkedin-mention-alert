from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src import cursor

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
ORG = "urn:li:organization:1"


def test_get_cursor_none_when_absent(db_conn):
    assert cursor.get_cursor(db_conn, ORG) is None


def test_set_and_get_cursor(db_conn):
    cursor.set_cursor(db_conn, ORG, NOW)
    assert cursor.get_cursor(db_conn, ORG) == NOW


def test_set_cursor_is_upsert(db_conn):
    cursor.set_cursor(db_conn, ORG, NOW)
    later = NOW + timedelta(hours=1)
    cursor.set_cursor(db_conn, ORG, later)
    assert cursor.get_cursor(db_conn, ORG) == later


def test_cursors_are_per_org(db_conn):
    cursor.set_cursor(db_conn, "urn:li:organization:1", NOW)
    cursor.set_cursor(db_conn, "urn:li:organization:2", NOW + timedelta(hours=2))
    assert cursor.get_cursor(db_conn, "urn:li:organization:1") == NOW
    assert cursor.get_cursor(db_conn, "urn:li:organization:2") == NOW + timedelta(hours=2)
