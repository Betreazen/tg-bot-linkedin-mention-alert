from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src import health

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def test_get_defaults_on_empty(db_conn):
    h = health.get(db_conn)
    assert h.last_successful_poll is None
    assert h.consecutive_failures == 0
    assert h.consecutive_rate_limited == 0
    assert h.last_cycle_at is None
    assert h.last_service_alert_at is None
    assert h.last_service_alert_kind is None
    assert h.last_refresh_warning_date is None


def test_record_failure_increments(db_conn):
    assert health.record_failure(db_conn) == 1
    assert health.record_failure(db_conn) == 2
    assert health.get(db_conn).consecutive_failures == 2


def test_record_rate_limited_increments(db_conn):
    assert health.record_rate_limited(db_conn) == 1
    assert health.record_rate_limited(db_conn) == 2
    assert health.get(db_conn).consecutive_rate_limited == 2
    assert health.get(db_conn).consecutive_failures == 0  # независимые счётчики


def test_record_success_resets_counters_and_alert_mark(db_conn):
    health.record_failure(db_conn)
    health.record_rate_limited(db_conn)
    health.set_service_alert_at(db_conn, NOW, "failure")
    health.record_success(db_conn, NOW)
    h = health.get(db_conn)
    assert h.consecutive_failures == 0
    assert h.consecutive_rate_limited == 0
    assert h.last_successful_poll == NOW
    assert h.last_service_alert_at is None  # новый сбой снова алертится сразу по порогу
    assert h.last_service_alert_kind is None


def test_service_alert_mark_stores_kind(db_conn):
    health.set_service_alert_at(db_conn, NOW, "rate_limit")
    h = health.get(db_conn)
    assert h.last_service_alert_at == NOW
    assert h.last_service_alert_kind == "rate_limit"


def test_record_cycle_heartbeat(db_conn):
    health.record_cycle(db_conn, NOW)
    assert health.get(db_conn).last_cycle_at == NOW
    later = NOW + timedelta(minutes=15)
    health.record_cycle(db_conn, later)
    assert health.get(db_conn).last_cycle_at == later


def test_refresh_warning_date_roundtrip(db_conn):
    assert health.get(db_conn).last_refresh_warning_date is None
    health.set_refresh_warning_date(db_conn, date(2026, 7, 24))
    assert health.get(db_conn).last_refresh_warning_date == date(2026, 7, 24)
