"""Настройка логирования с редакцией секретов.

Защита в глубину против утечки токенов в stdout/`docker logs`:
- httpx/httpcore логируют URL запроса на INFO; Telegram-токен лежит в ПУТИ URL, поэтому
  их уровень поднимается до WARNING (запрос-лог с токеном не пишется вовсе);
- на хендлеры вешается фильтр, который затирает известные секреты в сообщении,
  в ТРЕЙСБЕКЕ (record.exc_text) и в stack_info — а не только в msg;
- ротирующиеся секреты (access/refresh-токены LinkedIn из SQLite) добавляются в фильтр
  на лету через register_secrets() при каждом чтении/записи token_store.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from src.config import Config


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def add_secret(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._scrub(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        # Трейсбек рендерится Formatter'ом мимо msg — скрабим его через exc_text:
        # Formatter использует уже готовый record.exc_text и не форматирует exc_info заново.
        if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
            record.exc_text = self._scrub(
                logging.Formatter().formatException(record.exc_info)
            )
        elif record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)
        return True

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


#: единый фильтр процесса; register_secrets() пополняет его на лету
_redactor: SecretRedactingFilter | None = None


def register_secrets(*secrets: str) -> None:
    """Добавить ротирующиеся секреты (токены из SQLite) в редакцию логов."""
    if _redactor is None:  # логирование ещё не настроено (например, в юнит-тестах)
        return
    for secret in secrets:
        _redactor.add_secret(secret)


def configure_logging(config: Config) -> None:
    global _redactor
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _redactor = SecretRedactingFilter(
        [config.telegram_bot_token, config.linkedin_client_secret]
    )
    for handler in logging.getLogger().handlers:
        for old in [f for f in handler.filters if isinstance(f, SecretRedactingFilter)]:
            handler.removeFilter(old)  # повторный вызов не копит фильтры
        handler.addFilter(_redactor)
    # Telegram-токен в пути URL: не даём httpx логировать запрос на INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
