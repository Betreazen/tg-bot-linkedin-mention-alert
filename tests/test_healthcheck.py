from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src import healthcheck


@pytest.fixture
def hc_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_ORG_URNS", "urn:li:organization:1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setenv("HEARTBEAT_PATH", str(heartbeat))
    return heartbeat


def _stamp(path: Path, at: datetime) -> None:
    path.write_text(at.isoformat(), encoding="utf-8")
    os.utime(path, (at.timestamp(), at.timestamp()))


def test_healthy_before_first_cycle(hc_env):
    assert healthcheck.check() is None  # файла нет — грейс на старт


def test_healthy_with_fresh_heartbeat(hc_env):
    _stamp(hc_env, datetime.now(UTC) - timedelta(minutes=5))
    assert healthcheck.check() is None


def test_unhealthy_when_heartbeat_stale(hc_env):
    # порог = 2 интервала (15 мин по умолчанию) = 30 мин
    _stamp(hc_env, datetime.now(UTC) - timedelta(minutes=45))
    reason = healthcheck.check()
    assert reason is not None and "молчит" in reason


def test_main_exit_codes(hc_env, capsys):
    assert healthcheck.main() == 0
    _stamp(hc_env, datetime.now(UTC) - timedelta(hours=2))
    assert healthcheck.main() == 1
    assert "healthcheck" in capsys.readouterr().out


def test_main_unhealthy_on_broken_config(monkeypatch, capsys):
    # load_dotenv ищет .env от файла модуля — глушим, чтобы не подхватить реальный
    monkeypatch.setattr(healthcheck, "load_dotenv", lambda: None)
    for key in ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "LINKEDIN_ORG_URNS",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    assert healthcheck.main() == 1  # сбой самой проверки = unhealthy
    assert "healthcheck" in capsys.readouterr().out
