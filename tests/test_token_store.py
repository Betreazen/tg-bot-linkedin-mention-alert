from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src import token_store
from src.models import TokenSet

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _token() -> TokenSet:
    return TokenSet(
        access_token="AT",
        refresh_token="RT",
        access_token_expires_at=NOW + timedelta(days=60),
        refresh_token_expires_at=NOW + timedelta(days=365),
        updated_at=NOW,
    )


def test_load_empty_returns_none(db_conn):
    assert token_store.load_token(db_conn) is None


def test_save_and_load_roundtrip(db_conn):
    token_store.save_token(db_conn, _token())
    assert token_store.load_token(db_conn) == _token()


def test_save_is_upsert_single_row(db_conn):
    token_store.save_token(db_conn, _token())
    updated = TokenSet(
        access_token="AT2",
        refresh_token="RT2",
        access_token_expires_at=NOW + timedelta(days=61),
        refresh_token_expires_at=NOW + timedelta(days=366),
        updated_at=NOW,
    )
    token_store.save_token(db_conn, updated)
    assert token_store.load_token(db_conn) == updated
    count = db_conn.execute("SELECT COUNT(*) AS c FROM oauth_token").fetchone()["c"]
    assert count == 1


def _updated(updated_at) -> TokenSet:
    return TokenSet(
        access_token="AT_NEW",
        refresh_token="RT_NEW",
        access_token_expires_at=NOW + timedelta(days=60),
        refresh_token_expires_at=NOW + timedelta(days=365),
        updated_at=updated_at,
    )


def test_save_with_matching_cas_succeeds(db_conn):
    token_store.save_token(db_conn, _token())
    token_store.save_token(
        db_conn, _updated(NOW + timedelta(hours=1)), expected_updated_at=NOW
    )
    stored = token_store.load_token(db_conn)
    assert stored is not None and stored.access_token == "AT_NEW"


def test_save_with_stale_cas_raises_conflict(db_conn):
    """Гонка с setup_auth.py: параллельно записанные токены не затираются."""
    token_store.save_token(db_conn, _token())
    parallel = _updated(NOW + timedelta(hours=2))  # «setup_auth записал свежую пару»
    token_store.save_token(db_conn, parallel)

    import pytest

    with pytest.raises(token_store.TokenConflictError):
        token_store.save_token(
            db_conn, _updated(NOW + timedelta(hours=1)), expected_updated_at=NOW
        )
    assert token_store.load_token(db_conn) == parallel  # ничего не перезаписано


def test_auth_state_roundtrip_and_single_use(db_conn):
    token_store.save_auth_state(db_conn, "s3cret-state", NOW)
    assert token_store.pop_auth_state(db_conn) == "s3cret-state"
    assert token_store.pop_auth_state(db_conn) is None  # одноразовый


def test_auth_state_overwritten_by_new_flow(db_conn):
    token_store.save_auth_state(db_conn, "first", NOW)
    token_store.save_auth_state(db_conn, "second", NOW + timedelta(minutes=1))
    assert token_store.pop_auth_state(db_conn) == "second"
