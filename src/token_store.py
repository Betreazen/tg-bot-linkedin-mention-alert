"""Персистенция oauth_token/oauth_state ↔ TokenSet — единственная точка доступа.

Все datetime хранятся как ISO 8601 в UTC; на чтении возвращаются tz-aware UTC.
Живые access/refresh-токены регистрируются в фильтре редакции логов (защита в глубину).
`save_token(expected_updated_at=...)` — оптимистичный CAS: бот не затрёт токены,
записанные параллельно (например, вручную перезапущенным setup_auth.py).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from src import logging_setup
from src.db import storage_errors
from src.errors import StorageError
from src.models import TokenSet


class TokenConflictError(StorageError):
    """CAS-конфликт: oauth_token изменён параллельно (например, setup_auth.py)."""
from src.timeutil import from_iso as _from_iso
from src.timeutil import to_iso as _to_iso


def load_token(conn: sqlite3.Connection) -> TokenSet | None:
    with storage_errors():
        row = conn.execute(
            "SELECT access_token, refresh_token, access_token_expires_at, "
            "refresh_token_expires_at, updated_at FROM oauth_token WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    token = TokenSet(
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        access_token_expires_at=_from_iso(row["access_token_expires_at"]),
        refresh_token_expires_at=_from_iso(row["refresh_token_expires_at"]),
        updated_at=_from_iso(row["updated_at"]),
    )
    logging_setup.register_secrets(token.access_token, token.refresh_token)
    return token


def save_token(
    conn: sqlite3.Connection,
    token: TokenSet,
    *,
    expected_updated_at: datetime | None = None,
) -> None:
    """UPSERT пары токенов; с expected_updated_at — только если строка не изменилась."""
    logging_setup.register_secrets(token.access_token, token.refresh_token)
    params = (
        token.access_token,
        token.refresh_token,
        _to_iso(token.access_token_expires_at),
        _to_iso(token.refresh_token_expires_at),
        _to_iso(token.updated_at),
    )
    upsert = (
        "INSERT INTO oauth_token (id, access_token, refresh_token, "
        "access_token_expires_at, refresh_token_expires_at, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "access_token=excluded.access_token, "
        "refresh_token=excluded.refresh_token, "
        "access_token_expires_at=excluded.access_token_expires_at, "
        "refresh_token_expires_at=excluded.refresh_token_expires_at, "
        "updated_at=excluded.updated_at"
    )
    with storage_errors():
        if expected_updated_at is None:
            conn.execute(upsert, params)
        else:
            changed = conn.execute(
                upsert + " WHERE oauth_token.updated_at = ?",
                (*params, _to_iso(expected_updated_at)),
            ).rowcount
            if changed == 0:
                conn.rollback()
                raise TokenConflictError(
                    "oauth_token изменён параллельно (например, setup_auth.py) — "
                    "запись отменена, цикл перечитает токены"
                )
        conn.commit()


def save_auth_state(conn: sqlite3.Connection, state: str, created_at: datetime) -> None:
    """Сохранить CSRF state на время ручного Authorization Code flow."""
    with storage_errors():
        conn.execute(
            "INSERT INTO oauth_state (id, state, created_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
            "created_at=excluded.created_at",
            (state, _to_iso(created_at)),
        )
        conn.commit()


def pop_auth_state(conn: sqlite3.Connection) -> str | None:
    """Забрать и удалить сохранённый state (одноразовый)."""
    with storage_errors():
        row = conn.execute("SELECT state FROM oauth_state WHERE id = 1").fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM oauth_state WHERE id = 1")
        conn.commit()
    return str(row["state"])
