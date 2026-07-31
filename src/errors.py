"""Таксономия ошибок бота (порт паттерна smm-dashboard/connectors/base.py).

Разделение важно для Health-слоя (SPEC §10):
- TransientError (сеть, таймаут, 5xx) — временный сбой; естественный backoff — сам
  15-минутный интервал опроса. Подряд идущие сбои считаются в порог алерта.
- RateLimitError (429) — уважаем quota: НЕ засчитывается в порог сбоев (SPEC §10),
  но подряд идущие rate-limited циклы считаются отдельно и тоже алертятся.
- StorageError (SQLite: диск, блокировка, порча) — временный сбой хранилища; проходит
  через тот же учёт, что и сетевые, чтобы не обойти алертинг.
- AuthError (401/403) — токен не принят источником; Scheduler реагирует форс-refresh'ем
  (SPEC §10), при неудаче — обычный постоянный сбой.
- PermanentError (нет прав, протух refresh, кривой запрос) — не ретраить, считается сбоем.
"""

from __future__ import annotations


class BotError(Exception):
    """Базовая ошибка бота."""


class TransientError(BotError):
    """Временная ошибка (сеть, таймаут, 5xx) — повторить на следующем цикле."""


class RateLimitError(TransientError):
    """429 от источника — уважаем quota; НЕ засчитывается в порог сбоев (SPEC §10).

    `retry_after` — задержка из заголовка Retry-After (сек), если источник её прислал.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class StorageError(TransientError):
    """Сбой SQLite (блокировка, диск, порча) — учитывается как временный сбой."""


class PermanentError(BotError):
    """Постоянная ошибка (нет прав, протух refresh, кривой запрос) — не ретраить."""


class AuthError(PermanentError):
    """401/403 — источник не принял токен; повод для реактивного refresh (SPEC §10)."""
