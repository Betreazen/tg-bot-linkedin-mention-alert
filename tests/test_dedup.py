from __future__ import annotations

from datetime import UTC, datetime

from src import dedup
from src.models import Notification

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _notif(nid="n1", org="urn:li:organization:1", act="urn:li:activity:1") -> Notification:
    return Notification(notification_id=nid, org_urn=org, activity_urn=act, detected_at=NOW)


def test_is_sent_false_then_true(db_conn):
    assert dedup.is_sent(db_conn, "n1") is False
    dedup.record_sent(db_conn, _notif(), NOW)
    assert dedup.is_sent(db_conn, "n1") is True


def test_record_sent_is_idempotent(db_conn):
    dedup.record_sent(db_conn, _notif(), NOW)
    dedup.record_sent(db_conn, _notif(), NOW)
    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM mentions_sent WHERE notification_id='n1'"
    ).fetchone()["c"]
    assert count == 1


def test_record_sent_persists_fields(db_conn):
    dedup.record_sent(db_conn, _notif(org="urn:li:organization:5", act="urn:li:activity:9"), NOW)
    row = db_conn.execute(
        "SELECT org_urn, source_post_urn FROM mentions_sent WHERE notification_id='n1'"
    ).fetchone()
    assert row["org_urn"] == "urn:li:organization:5"
    assert row["source_post_urn"] == "urn:li:activity:9"


def test_prune_removes_only_old_records(db_conn):
    from datetime import timedelta

    old = NOW - timedelta(days=dedup.RETENTION_DAYS + 1)
    dedup.record_sent(db_conn, _notif("old"), old)
    dedup.record_sent(db_conn, _notif("fresh", act="urn:li:activity:2"), NOW)

    deleted = dedup.prune(db_conn, NOW)

    assert deleted == 1
    assert dedup.is_sent(db_conn, "old") is False
    assert dedup.is_sent(db_conn, "fresh") is True
