"""LinkedIn OAuth: обмен authorization code и refresh grant (SPEC §8.1).

Порт проверенного паттерна smm-dashboard (connectors/linkedin.py):
- token endpoint https://www.linkedin.com/oauth/v2/accessToken;
- LinkedIn может ВЕРНУТЬ НОВЫЙ refresh_token при обмене — его надо сохранить,
  иначе доступ теряется до ручного переконсента владельца;
- протухший/отозванный refresh → HTTP 400 → PermanentError (нужен новый консент).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from src.errors import PermanentError
from src.http import request_json
from src.models import TokenSet

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"

#: дефолтные сроки, если token endpoint не вернул *_expires_in
_DEFAULT_ACCESS_TTL = timedelta(days=60)
_DEFAULT_REFRESH_TTL = timedelta(days=365)


def build_authorization_url(
    *, client_id: str, redirect_uri: str, scope: str, state: str
) -> str:
    """URL Authorization Code flow с CSRF state (RFC 6749 §10.12)."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


def exchange_code(
    client: httpx.Client,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    now: datetime | None = None,
) -> TokenSet:
    """Обмен authorization code → пара токенов (разовая первичная авторизация)."""
    now = now or datetime.now(UTC)
    data = request_json(
        client,
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    return _parse(data, now=now)


def refresh(
    client: httpx.Client,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    now: datetime | None = None,
    prev_refresh_expires_at: datetime | None = None,
) -> TokenSet:
    """Обмен refresh_token → новый access_token (и, возможно, новый refresh_token)."""
    now = now or datetime.now(UTC)
    data = request_json(
        client,
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    return _parse(
        data,
        now=now,
        prev_refresh_token=refresh_token,
        prev_refresh_expires_at=prev_refresh_expires_at,
    )


def _parse(
    data: Mapping[str, object],
    *,
    now: datetime,
    prev_refresh_token: str = "",
    prev_refresh_expires_at: datetime | None = None,
) -> TokenSet:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise PermanentError("token endpoint LinkedIn не вернул access_token")

    new_refresh = data.get("refresh_token")
    if isinstance(new_refresh, str) and new_refresh:
        refresh_token = new_refresh
        rotated = True
    else:
        refresh_token = prev_refresh_token
        rotated = False
    if not refresh_token:
        raise PermanentError("нет refresh_token ни в ответе, ни в сохранённом состоянии")

    if rotated:
        refresh_expires_at = now + _ttl(
            data.get("refresh_token_expires_in"), _DEFAULT_REFRESH_TTL,
            field="refresh_token_expires_in",
        )
    elif prev_refresh_expires_at is not None:
        refresh_expires_at = prev_refresh_expires_at
    else:
        refresh_expires_at = now + _DEFAULT_REFRESH_TTL

    return TokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_at=now + _ttl(
            data.get("expires_in"), _DEFAULT_ACCESS_TTL, field="expires_in"
        ),
        refresh_token_expires_at=refresh_expires_at,
        updated_at=now,
    )


def _ttl(value: object, default: timedelta, *, field: str) -> timedelta:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        # молчаливая догадка о сроке жизни токена опасна — оставляем след в логах
        log.warning("oauth.ttl_fallback field=%s value=%r default=%s", field, value, default)
        return default
    return timedelta(seconds=int(value))
