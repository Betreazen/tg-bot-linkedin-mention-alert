"""bot_health (SPEC §6, §10): счётчики сбоев/rate-limit и метки времени для алертинга.

- consecutive_failures / consecutive_rate_limited — подряд идущие циклы каждого вида;
  оба сбрасываются только полным успехом цикла.
- last_cycle_at — heartbeat каждого цикла независимо от исхода (для Docker HEALTHCHECK).
- last_service_alert_at/_kind — метка последнего доставленного служебного алерта и его
  вид ('failure'/'rate_limit'): даёт суточный повтор при затяжном сбое, повторную
  попытку при неудачной доставке, а смена вида сбоя алертится сразу, без троттлинга.
- last_refresh_warning_date — суточный троттлинг предупреждения о refresh_token,
  переживает рестарт контейнера.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from src.db import storage_errors
from src.timeutil import from_iso, to_iso


@dataclass(frozen=True, slots=True)
class Health:
    last_successful_poll: datetime | None
    consecutive_failures: int
    consecutive_rate_limited: int
    last_cycle_at: datetime | None
    last_service_alert_at: datetime | None
    last_service_alert_kind: str | None
    last_refresh_warning_date: date | None


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR IGNORE INTO bot_health (id) VALUES (1)")


def get(conn: sqlite3.Connection) -> Health:
    with storage_errors():
        _ensure(conn)
        row = conn.execute(
            "SELECT last_successful_poll, consecutive_failures, consecutive_rate_limited, "
            "last_cycle_at, last_service_alert_at, last_service_alert_kind, "
            "last_refresh_warning_date FROM bot_health WHERE id = 1"
        ).fetchone()
    return Health(
        last_successful_poll=_maybe_dt(row["last_successful_poll"]),
        consecutive_failures=int(row["consecutive_failures"]),
        consecutive_rate_limited=int(row["consecutive_rate_limited"]),
        last_cycle_at=_maybe_dt(row["last_cycle_at"]),
        last_service_alert_at=_maybe_dt(row["last_service_alert_at"]),
        last_service_alert_kind=row["last_service_alert_kind"],
        last_refresh_warning_date=_maybe_date(row["last_refresh_warning_date"]),
    )


def record_cycle(conn: sqlite3.Connection, at: datetime) -> None:
    """Heartbeat: цикл начался (независимо от исхода) — для внешнего HEALTHCHECK."""
    with storage_errors():
        _ensure(conn)
        conn.execute("UPDATE bot_health SET last_cycle_at = ? WHERE id = 1", (to_iso(at),))
        conn.commit()


def record_success(conn: sqlite3.Connection, at: datetime) -> None:
    """Полный успех: сброс обоих счётчиков и разрешение нового служебного алерта."""
    with storage_errors():
        _ensure(conn)
        conn.execute(
            "UPDATE bot_health SET last_successful_poll = ?, consecutive_failures = 0, "
            "consecutive_rate_limited = 0, last_service_alert_at = NULL, "
            "last_service_alert_kind = NULL WHERE id = 1",
            (to_iso(at),),
        )
        conn.commit()


def record_failure(conn: sqlite3.Connection) -> int:
    """Инкремент счётчика сбоев; возвращает новое значение."""
    with storage_errors():
        _ensure(conn)
        conn.execute(
            "UPDATE bot_health SET consecutive_failures = consecutive_failures + 1 "
            "WHERE id = 1"
        )
        conn.commit()
        return int(
            conn.execute(
                "SELECT consecutive_failures FROM bot_health WHERE id = 1"
            ).fetchone()["consecutive_failures"]
        )


def record_rate_limited(conn: sqlite3.Connection) -> int:
    """Инкремент счётчика подряд rate-limited циклов; возвращает новое значение."""
    with storage_errors():
        _ensure(conn)
        conn.execute(
            "UPDATE bot_health SET consecutive_rate_limited = consecutive_rate_limited + 1 "
            "WHERE id = 1"
        )
        conn.commit()
        return int(
            conn.execute(
                "SELECT consecutive_rate_limited FROM bot_health WHERE id = 1"
            ).fetchone()["consecutive_rate_limited"]
        )


def set_service_alert_at(conn: sqlite3.Connection, at: datetime, kind: str) -> None:
    """Помечается только после УСПЕШНОЙ доставки алерта — неудача повторит попытку."""
    with storage_errors():
        _ensure(conn)
        conn.execute(
            "UPDATE bot_health SET last_service_alert_at = ?, "
            "last_service_alert_kind = ? WHERE id = 1",
            (to_iso(at), kind),
        )
        conn.commit()


def set_refresh_warning_date(conn: sqlite3.Connection, day: date) -> None:
    """Помечается только после УСПЕШНОЙ доставки предупреждения (раз в сутки)."""
    with storage_errors():
        _ensure(conn)
        conn.execute(
            "UPDATE bot_health SET last_refresh_warning_date = ? WHERE id = 1",
            (day.isoformat(),),
        )
        conn.commit()


def _maybe_dt(value: str | None) -> datetime | None:
    return from_iso(value) if value is not None else None


def _maybe_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None
