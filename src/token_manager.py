"""Token Manager (SPEC §3.2): выдаёт валидный access_token, обновляя при нужде.

Политика: если access_token истекает в пределах ACCESS_TOKEN_REFRESH_SKEW —
меняем по refresh_token. Дополнительно `force_refresh()` — реактивное обновление
по 401/403 от API (SPEC §10). Возможный ротированный refresh_token сохраняется
сразу; запись защищена CAS (updated_at) и одной повторной попыткой — ротированный
токен, потерянный из-за сбоя записи, означает потерю доступа до переконсента.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from src import oauth, token_store
from src.config import Config
from src.errors import PermanentError, StorageError
from src.http import make_client
from src.models import TokenSet
from src.token_store import TokenConflictError

log = logging.getLogger(__name__)

#: запас: обновляем токен заранее, чтобы цикл не начался с протухшим access
ACCESS_TOKEN_REFRESH_SKEW = timedelta(minutes=15)


class TokenManager:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._config = config
        self._transport = transport
        self._now = now_fn or (lambda: datetime.now(UTC))

    def get_valid_access_token(self) -> str:
        """Актуальный access_token; при близком истечении — рефреш."""
        token = self._require_token()
        now = self._now()
        if token.access_token_expires_at - now > ACCESS_TOKEN_REFRESH_SKEW:
            return token.access_token
        return self._refresh(token, now).access_token

    def force_refresh(self) -> str:
        """Реактивный рефреш по 401/403 (SPEC §10) независимо от локальных сроков."""
        token = self._require_token()
        return self._refresh(token, self._now()).access_token

    def refresh_token_days_left(self) -> int | None:
        """Сколько дней осталось до истечения refresh_token (для проактивного алерта)."""
        token = token_store.load_token(self._conn)
        if token is None:
            return None
        return (token.refresh_token_expires_at - self._now()).days

    def _require_token(self) -> TokenSet:
        token = token_store.load_token(self._conn)
        if token is None:
            raise PermanentError(
                "нет сохранённых токенов — выполните setup_auth.py (первичная авторизация)"
            )
        return token

    def _refresh(self, token: TokenSet, now: datetime) -> TokenSet:
        with make_client(self._transport) as client:
            new_token = oauth.refresh(
                client,
                refresh_token=token.refresh_token,
                client_id=self._config.linkedin_client_id,
                client_secret=self._config.linkedin_client_secret,
                now=now,
                prev_refresh_expires_at=token.refresh_token_expires_at,
            )
        return self._save_refreshed(new_token, expected_updated_at=token.updated_at)

    def _save_refreshed(self, new_token: TokenSet, *, expected_updated_at: datetime) -> TokenSet:
        """Ротированный refresh_token нельзя терять: одна повторная попытка записи.

        CAS-конфликт — не сбой: параллельно записанные токены (setup_auth.py со свежим
        консентом) авторитетнее нашего рефреша, работаем с ними.
        """
        try:
            token_store.save_token(
                self._conn, new_token, expected_updated_at=expected_updated_at
            )
            return new_token
        except TokenConflictError:
            log.warning("token.concurrent_write — использую токены, записанные параллельно")
            current = token_store.load_token(self._conn)
            return current if current is not None else new_token
        except StorageError as exc:
            log.error("token.save_failed retry=1 error=%s", exc)
            token_store.save_token(
                self._conn, new_token, expected_updated_at=expected_updated_at
            )
            return new_token
