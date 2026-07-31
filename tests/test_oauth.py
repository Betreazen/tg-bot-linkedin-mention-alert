from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src import oauth
from src.errors import PermanentError
from src.http import make_client

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _client(payload, status=200):
    return make_client(httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_exchange_code_parses_tokens():
    payload = {
        "access_token": "AT",
        "expires_in": 5184000,
        "refresh_token": "RT",
        "refresh_token_expires_in": 31536000,
    }
    with _client(payload) as c:
        token = oauth.exchange_code(
            c, code="c", redirect_uri="http://localhost",
            client_id="id", client_secret="s", now=NOW,
        )
    assert token.access_token == "AT"
    assert token.refresh_token == "RT"
    assert token.access_token_expires_at == NOW + timedelta(seconds=5184000)
    assert token.refresh_token_expires_at == NOW + timedelta(seconds=31536000)
    assert token.updated_at == NOW


def test_exchange_code_requires_refresh_token():
    with _client({"access_token": "AT", "expires_in": 5184000}) as c:
        with pytest.raises(PermanentError):
            oauth.exchange_code(
                c, code="c", redirect_uri="u", client_id="id", client_secret="s", now=NOW
            )


def test_refresh_keeps_old_token_without_rotation():
    prev_exp = NOW + timedelta(days=100)
    with _client({"access_token": "AT3", "expires_in": 5184000}) as c:
        token = oauth.refresh(
            c, refresh_token="RT_OLD", client_id="id", client_secret="s",
            now=NOW, prev_refresh_expires_at=prev_exp,
        )
    assert token.access_token == "AT3"
    assert token.refresh_token == "RT_OLD"
    assert token.refresh_token_expires_at == prev_exp


def test_refresh_persists_rotated_token():
    payload = {
        "access_token": "AT2",
        "expires_in": 5184000,
        "refresh_token": "RT_NEW",
        "refresh_token_expires_in": 31536000,
    }
    with _client(payload) as c:
        token = oauth.refresh(
            c, refresh_token="RT_OLD", client_id="id", client_secret="s",
            now=NOW, prev_refresh_expires_at=NOW + timedelta(days=1),
        )
    assert token.refresh_token == "RT_NEW"
    assert token.refresh_token_expires_at == NOW + timedelta(seconds=31536000)


def test_refresh_without_rotation_or_prev_expiry_uses_default_refresh_ttl():
    with _client({"access_token": "AT3", "expires_in": 5184000}) as c:
        token = oauth.refresh(
            c, refresh_token="RT_OLD", client_id="id", client_secret="s", now=NOW
        )
    assert token.refresh_token == "RT_OLD"
    assert token.refresh_token_expires_at == NOW + timedelta(days=365)


def test_refresh_400_is_permanent():
    with _client({"error": "invalid_grant"}, status=400) as c:
        with pytest.raises(PermanentError):
            oauth.refresh(c, refresh_token="RT", client_id="id", client_secret="s", now=NOW)


def test_missing_access_token_is_permanent():
    with _client({"expires_in": 5184000, "refresh_token": "RT"}) as c:
        with pytest.raises(PermanentError):
            oauth.exchange_code(
                c, code="c", redirect_uri="u", client_id="id", client_secret="s", now=NOW
            )


def test_missing_expires_in_uses_default_access_ttl():
    with _client({"access_token": "AT", "refresh_token": "RT"}) as c:
        token = oauth.exchange_code(
            c, code="c", redirect_uri="u", client_id="id", client_secret="s", now=NOW
        )
    assert token.access_token_expires_at == NOW + timedelta(days=60)


def test_non_positive_expires_in_uses_default_access_ttl():
    with _client({"access_token": "AT", "refresh_token": "RT", "expires_in": 0}) as c:
        token = oauth.exchange_code(
            c, code="c", redirect_uri="u", client_id="id", client_secret="s", now=NOW
        )
    assert token.access_token_expires_at == NOW + timedelta(days=60)
