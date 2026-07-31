from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.config import load_config
from src.errors import PermanentError
from src.models import Notification
from src.notifier import (
    Notifier,
    format_mention,
    format_refresh_warning,
    format_service_alert,
)


def _notif() -> Notification:
    return Notification(
        notification_id="n1",
        org_urn="urn:li:organization:1",
        activity_urn="urn:li:activity:42",
        detected_at=datetime(2026, 7, 24, 12, 42, tzinfo=UTC),
    )


def test_format_mention_converts_timezone():
    text = format_mention(_notif(), ZoneInfo("Europe/Kyiv"))
    assert "https://www.linkedin.com/feed/update/urn:li:activity:42/" in text
    assert "24.07.2026 15:42" in text  # Киев летом UTC+3: 12:42 → 15:42


def test_format_service_alert_with_and_without_last_success():
    tz = ZoneInfo("Europe/Kyiv")
    with_last = format_service_alert(
        "3 неудачных опроса подряд", datetime(2026, 7, 24, 11, 12, tzinfo=UTC), tz
    )
    assert "проблема" in with_last
    assert "24.07.2026 14:12" in with_last  # 11:12 UTC → 14:12 Киев
    without_last = format_service_alert("токен", None, tz)
    assert "Последний успешный опрос: —" in without_last


def test_format_refresh_warning():
    text = format_refresh_warning(7)
    assert "7 дн." in text
    assert "реавторизация" in text


def test_format_refresh_warning_expired():
    assert "уже истёк" in format_refresh_warning(0)
    assert "уже истёк" in format_refresh_warning(-2)


def test_send_mention_posts_to_telegram(valid_env):
    captured: dict[str, str] = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["content"] = request.content.decode()
        return httpx.Response(200, json={"ok": True, "result": {}})

    notifier = Notifier(load_config(valid_env), transport=httpx.MockTransport(handler))
    notifier.send_mention(_notif())

    assert "/bottg-token/sendMessage" in captured["url"]
    assert "chat_id" in captured["content"]


def test_send_service_alert_and_refresh_warning_post(valid_env):
    calls: list[str] = []

    def handler(request):
        calls.append(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    notifier = Notifier(load_config(valid_env), transport=httpx.MockTransport(handler))
    notifier.send_service_alert("боль", None)
    notifier.send_refresh_warning(5)
    assert len(calls) == 2


def test_send_text_scrubs_bot_token_on_error(valid_env):
    def handler(request):
        return httpx.Response(400, text="bad request")

    notifier = Notifier(load_config(valid_env), transport=httpx.MockTransport(handler))
    with pytest.raises(PermanentError) as exc:
        notifier.send_text("hi")
    assert "tg-token" not in str(exc.value)
    assert "***" in str(exc.value)
    # цепочка разорвана (from None) — нескрабленный токен не утечёт в traceback/log.exception
    assert exc.value.__cause__ is None


def test_send_text_retries_once_on_network_error(valid_env):
    """SPEC §8.3: ретрай при сетевых ошибках."""
    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True})

    notifier = Notifier(
        load_config(valid_env),
        transport=httpx.MockTransport(handler),
        sleep_fn=sleeps.append,
    )
    notifier.send_text("hi")
    assert len(attempts) == 2
    assert sleeps == [2.0]


def test_send_text_respects_retry_after_on_429(valid_env):
    """SPEC §8.3: уважать retry_after при 429."""
    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, text="slow", headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    notifier = Notifier(
        load_config(valid_env),
        transport=httpx.MockTransport(handler),
        sleep_fn=sleeps.append,
    )
    notifier.send_text("hi")
    assert len(attempts) == 2
    assert sleeps == [7]


def test_send_text_gives_up_on_huge_retry_after(valid_env):
    """Слишком большой retry_after не ждём в цикле — отдаём 429 наверх."""
    from src.errors import RateLimitError

    def handler(request):
        return httpx.Response(429, text="slow", headers={"Retry-After": "600"})

    sleeps: list[float] = []
    notifier = Notifier(
        load_config(valid_env),
        transport=httpx.MockTransport(handler),
        sleep_fn=sleeps.append,
    )
    with pytest.raises(RateLimitError):
        notifier.send_text("hi")
    assert sleeps == []


def test_telegram_auth_error_downgraded_to_permanent(valid_env):
    """Telegram 401 — ошибка конфигурации бота, не повод рефрешить LinkedIn-токен."""
    from src.errors import AuthError

    def handler(request):
        return httpx.Response(401, text="Unauthorized")

    notifier = Notifier(load_config(valid_env), transport=httpx.MockTransport(handler))
    with pytest.raises(PermanentError) as exc:
        notifier.send_text("hi")
    assert not isinstance(exc.value, AuthError)
    assert "tg-token" not in str(exc.value)
