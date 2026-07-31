from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src import token_store
from src.config import load_config
from src.errors import PermanentError
from src.models import TokenSet
from src.token_manager import TokenManager

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _seed(conn, *, access_expires_in_days: int, refresh_expires_in_days: int = 200) -> None:
    token_store.save_token(
        conn,
        TokenSet(
            access_token="AT",
            refresh_token="RT",
            access_token_expires_at=NOW + timedelta(days=access_expires_in_days),
            refresh_token_expires_at=NOW + timedelta(days=refresh_expires_in_days),
            updated_at=NOW,
        ),
    )


def _fail_transport() -> httpx.MockTransport:
    def handler(request):
        raise AssertionError("сеть не должна вызываться при валидном токене")

    return httpx.MockTransport(handler)


def test_no_token_raises(db_conn, valid_env):
    tm = TokenManager(db_conn, load_config(valid_env), now_fn=lambda: NOW)
    with pytest.raises(PermanentError):
        tm.get_valid_access_token()


def test_valid_token_returned_without_network(db_conn, valid_env):
    _seed(db_conn, access_expires_in_days=30)
    tm = TokenManager(
        db_conn, load_config(valid_env), transport=_fail_transport(), now_fn=lambda: NOW
    )
    assert tm.get_valid_access_token() == "AT"


def test_expiring_token_is_refreshed_and_persisted(db_conn, valid_env):
    _seed(db_conn, access_expires_in_days=0)  # истекает сейчас → в пределах skew
    payload = {
        "access_token": "AT_NEW",
        "expires_in": 5184000,
        "refresh_token": "RT_NEW",
        "refresh_token_expires_in": 31536000,
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    tm = TokenManager(db_conn, load_config(valid_env), transport=transport, now_fn=lambda: NOW)
    assert tm.get_valid_access_token() == "AT_NEW"
    stored = token_store.load_token(db_conn)
    assert stored is not None
    assert stored.access_token == "AT_NEW"
    assert stored.refresh_token == "RT_NEW"


def test_refresh_token_days_left(db_conn, valid_env):
    _seed(db_conn, access_expires_in_days=30, refresh_expires_in_days=200)
    tm = TokenManager(db_conn, load_config(valid_env), now_fn=lambda: NOW)
    assert tm.refresh_token_days_left() == 200


def test_refresh_token_days_left_none_when_empty(db_conn, valid_env):
    tm = TokenManager(db_conn, load_config(valid_env), now_fn=lambda: NOW)
    assert tm.refresh_token_days_left() is None


def _seed_minutes(conn, *, access_expires_in_minutes: int) -> None:
    token_store.save_token(
        conn,
        TokenSet(
            access_token="AT",
            refresh_token="RT",
            access_token_expires_at=NOW + timedelta(minutes=access_expires_in_minutes),
            refresh_token_expires_at=NOW + timedelta(days=200),
            updated_at=NOW,
        ),
    )


def test_skew_boundary_outside_skew_no_refresh(db_conn, valid_env):
    """Граница ACCESS_TOKEN_REFRESH_SKEW (15 мин): 16 мин запаса → без сети."""
    _seed_minutes(db_conn, access_expires_in_minutes=16)
    tm = TokenManager(
        db_conn, load_config(valid_env), transport=_fail_transport(), now_fn=lambda: NOW
    )
    assert tm.get_valid_access_token() == "AT"


def test_skew_boundary_inside_skew_refreshes(db_conn, valid_env):
    """14 мин запаса — внутри skew → рефреш."""
    _seed_minutes(db_conn, access_expires_in_minutes=14)
    payload = {"access_token": "AT_NEW", "expires_in": 5184000}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    tm = TokenManager(db_conn, load_config(valid_env), transport=transport, now_fn=lambda: NOW)
    assert tm.get_valid_access_token() == "AT_NEW"


def test_force_refresh_ignores_local_expiry(db_conn, valid_env):
    """Реактивный refresh по 401/403: локальный срок ещё «валиден», но рефрешим."""
    _seed(db_conn, access_expires_in_days=30)
    payload = {"access_token": "AT_FORCED", "expires_in": 5184000}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    tm = TokenManager(db_conn, load_config(valid_env), transport=transport, now_fn=lambda: NOW)
    assert tm.force_refresh() == "AT_FORCED"
    stored = token_store.load_token(db_conn)
    assert stored is not None and stored.access_token == "AT_FORCED"


def test_refresh_save_retries_once_on_storage_error(db_conn, valid_env, monkeypatch):
    """Ротированный refresh_token нельзя терять: сбой записи → вторая попытка."""
    from src import token_store as token_store_module
    from src.errors import StorageError

    _seed(db_conn, access_expires_in_days=0)
    payload = {"access_token": "AT_NEW", "expires_in": 5184000, "refresh_token": "RT_NEW"}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))

    original = token_store_module.save_token
    fails = {"left": 1}

    def flaky_save(conn, token, **kwargs):
        if fails["left"] > 0:
            fails["left"] -= 1
            raise StorageError("database is locked")
        original(conn, token, **kwargs)

    monkeypatch.setattr(token_store_module, "save_token", flaky_save)
    tm = TokenManager(db_conn, load_config(valid_env), transport=transport, now_fn=lambda: NOW)
    assert tm.get_valid_access_token() == "AT_NEW"
    stored = token_store.load_token(db_conn)
    assert stored is not None and stored.refresh_token == "RT_NEW"  # не потерян


def test_refresh_yields_to_concurrently_written_tokens(db_conn, valid_env):
    """CAS-конфликт: параллельная запись (setup_auth) авторитетнее нашего рефреша."""
    _seed(db_conn, access_expires_in_days=0)  # в пределах skew → пойдёт рефреш

    payload = {"access_token": "AT_REFRESHED", "expires_in": 5184000}

    def handler(request):
        # пока «шёл сетевой запрос», setup_auth записал свежую пару
        token_store.save_token(
            db_conn,
            TokenSet(
                access_token="AT_MANUAL",
                refresh_token="RT_MANUAL",
                access_token_expires_at=NOW + timedelta(days=60),
                refresh_token_expires_at=NOW + timedelta(days=365),
                updated_at=NOW + timedelta(seconds=30),
            ),
        )
        return httpx.Response(200, json=payload)

    tm = TokenManager(
        db_conn, load_config(valid_env),
        transport=httpx.MockTransport(handler), now_fn=lambda: NOW,
    )
    assert tm.get_valid_access_token() == "AT_MANUAL"  # ручная пара не затёрта
    stored = token_store.load_token(db_conn)
    assert stored is not None and stored.access_token == "AT_MANUAL"
