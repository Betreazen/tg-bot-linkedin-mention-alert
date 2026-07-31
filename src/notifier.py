"""Notifier (SPEC §3.2, §8.3, §9): форматирование и отправка сообщений в Telegram.

Токен бота лежит в ПУТИ URL Telegram (`/bot{token}/sendMessage`), поэтому при любой
ошибке текст затирается, чтобы токен не утёк в логи или в служебный алерт в чат.

SPEC §8.3: одна повторная попытка при сетевой ошибке; при 429 уважается retry_after
(если он разумный — иначе отдаём RateLimitError, естественный backoff = интервал опроса).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

import httpx

from src.config import Config
from src.errors import AuthError, BotError, PermanentError, RateLimitError, TransientError
from src.http import make_client, request_json
from src.models import Notification

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
_TIME_FMT = "%d.%m.%Y %H:%M"
#: пауза перед повтором после сетевой ошибки
NETWORK_RETRY_DELAY_S = 2.0
#: дольше этого retry_after не ждём в цикле — отдаём 429 наверх (ждать будет интервал)
MAX_RETRY_AFTER_S = 30


def format_mention(notification: Notification, tz: tzinfo) -> str:
    when = notification.detected_at.astimezone(tz).strftime(_TIME_FMT)
    return f"🔔 Новое упоминание LinkedIn\n🔗 {notification.post_url}\n🕒 {when}"


def format_service_alert(reason: str, last_success: datetime | None, tz: tzinfo) -> str:
    when = last_success.astimezone(tz).strftime(_TIME_FMT) if last_success else "—"
    return (
        "⚠️ LinkedIn Mentions Bot: проблема\n"
        f"{reason}\n"
        f"Последний успешный опрос: {when}\n"
        "Требуется проверка."
    )


def format_refresh_warning(days: int) -> str:
    head = (
        "refresh_token уже истёк — бот не может обновлять доступ"
        if days <= 0
        else f"скоро истекает refresh_token (осталось {days} дн.)"
    )
    return f"⏳ LinkedIn Mentions Bot: {head}.\nНужна ручная реавторизация (Authorization Code flow)."


class Notifier:
    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config
        self._tz = ZoneInfo(config.timezone)
        self._transport = transport
        self._sleep = sleep_fn or time.sleep

    def send_mention(self, notification: Notification) -> None:
        self.send_text(format_mention(notification, self._tz))

    def send_service_alert(self, reason: str, last_success: datetime | None) -> None:
        self.send_text(format_service_alert(reason, last_success, self._tz))

    def send_refresh_warning(self, days: int) -> None:
        self.send_text(format_refresh_warning(days))

    def send_text(self, text: str) -> None:
        try:
            self._send_with_retry(text)
        except BotError as exc:
            # Telegram 401/403 — ошибка конфигурации бота, а не повод рефрешить LinkedIn
            cls = PermanentError if isinstance(exc, AuthError) else type(exc)
            # from None: рвём цепочку, иначе нескрабленный __cause__ утечёт в log.exception
            raise cls(self._scrub(str(exc))) from None

    def _send_with_retry(self, text: str) -> None:
        try:
            self._post(text)
        except RateLimitError as exc:
            if exc.retry_after is None or exc.retry_after > MAX_RETRY_AFTER_S:
                raise
            log.info("telegram.retry_after seconds=%d", exc.retry_after)
            self._sleep(exc.retry_after)
            self._post(text)
        except TransientError:
            log.info("telegram.network_retry delay=%.0fs", NETWORK_RETRY_DELAY_S)
            self._sleep(NETWORK_RETRY_DELAY_S)
            self._post(text)

    def _post(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self._config.telegram_bot_token}/sendMessage"
        with make_client(self._transport) as client:
            request_json(
                client,
                "POST",
                url,
                data={
                    "chat_id": self._config.telegram_chat_id,
                    "text": text,
                    "disable_web_page_preview": "false",
                },
            )

    def _scrub(self, message: str) -> str:
        return message.replace(self._config.telegram_bot_token, "***")
