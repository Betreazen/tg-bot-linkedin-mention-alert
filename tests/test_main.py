from __future__ import annotations

import threading

import main as main_module
from main import run_loop


class FakeNotifier:
    def __init__(self, *, fail: bool = False):
        self.alerts: list[str] = []
        self.attempts = 0
        self._fail = fail

    def send_service_alert(self, reason, last_success):
        self.attempts += 1
        if self._fail:
            raise RuntimeError("telegram недоступен")
        self.alerts.append(reason)


class ScriptedScheduler:
    """Сценарий по циклам: 'ok' — успех, 'boom' — исключение; затем стоп."""

    def __init__(self, script: list[str], stop: threading.Event):
        self._script = script
        self._stop = stop
        self.cycles = 0

    def run_cycle(self):
        step = self._script[self.cycles]
        self.cycles += 1
        if self.cycles >= len(self._script):
            self._stop.set()
        if step == "boom":
            raise RuntimeError("сломан даже учёт сбоев")


def _run(script: list[str], notifier: FakeNotifier) -> ScriptedScheduler:
    stop = threading.Event()
    scheduler = ScriptedScheduler(script, stop)
    run_loop(scheduler, notifier, stop, interval_seconds=0.001, alert_threshold=3)
    return scheduler


def test_loop_survives_unexpected_errors_and_alerts_at_threshold():
    """Последний рубеж (C1): цикл не падает, по порогу уходит best-effort алерт."""
    notifier = FakeNotifier()
    scheduler = _run(["boom", "boom", "boom", "boom", "ok"], notifier)
    assert scheduler.cycles == 5  # ни одно исключение не уронило цикл
    assert len(notifier.alerts) == 1  # ровно на пороге (3), без спама на 4-м


def test_loop_alert_failure_does_not_crash():
    notifier = FakeNotifier(fail=True)
    scheduler = _run(["boom", "boom", "boom", "ok"], notifier)
    assert scheduler.cycles == 4
    assert notifier.attempts == 1


def test_loop_success_resets_unexpected_counter():
    notifier = FakeNotifier()
    _run(["boom", "boom", "ok", "boom", "boom", "ok"], notifier)
    assert notifier.alerts == []  # серия прерывалась успехом — порог не достигнут


def test_main_wires_and_stops(monkeypatch, tmp_path):
    """main(): конфиг, БД, graceful-выход — с подменённым run_loop."""
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_ORG_URNS", "urn:li:organization:1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))

    called: dict[str, object] = {}

    def fake_run_loop(scheduler, notifier, stop, *, interval_seconds, alert_threshold):
        called["interval"] = interval_seconds
        called["threshold"] = alert_threshold

    monkeypatch.setattr(main_module, "run_loop", fake_run_loop)
    assert main_module.main() == 0
    assert called["interval"] == 15 * 60
    assert called["threshold"] == 3
    assert (tmp_path / "bot.db").exists()
