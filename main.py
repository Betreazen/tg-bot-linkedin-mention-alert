"""Точка входа: цикл polling (SPEC §3.2 Scheduler).

Простой `while not stop: run_cycle(); wait(interval)` без внешнего крона. Цикл не падает
целиком: неожиданная ошибка логируется и — последний рубеж, если сломан даже учёт сбоев
в БД — по порогу подряд идущих сбоев уходит best-effort алерт в Telegram.
SIGTERM/SIGINT (docker stop / Ctrl+C) завершают цикл аккуратно.
"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from dotenv import load_dotenv

from src.config import load_config
from src.db import connect, init_db
from src.linkedin_client import LinkedInClient
from src.logging_setup import configure_logging
from src.notifier import Notifier
from src.scheduler import Scheduler
from src.token_manager import TokenManager

log = logging.getLogger("linkedin_mentions_bot")


def run_loop(
    scheduler: Scheduler,
    notifier: Notifier,
    stop: threading.Event,
    *,
    interval_seconds: float,
    alert_threshold: int,
) -> None:
    """Основной цикл; отделён от main() для тестируемости."""
    unexpected_failures = 0
    while not stop.is_set():
        try:
            scheduler.run_cycle()
            unexpected_failures = 0
        except Exception:  # noqa: BLE001 — последний рубеж, цикл не должен падать целиком
            unexpected_failures += 1
            log.exception("cycle.unexpected_error count=%d", unexpected_failures)
            if unexpected_failures == alert_threshold:
                _try_alert(notifier, unexpected_failures)
        stop.wait(interval_seconds)


def _try_alert(notifier: Notifier, count: int) -> None:
    """Best-effort алерт, когда сломан даже учёт сбоев (например, мертва БД)."""
    try:
        notifier.send_service_alert(
            f"неожиданная ошибка цикла {count} раз подряд — подробности в docker logs",
            None,
        )
    except Exception:  # noqa: BLE001 — алерт не должен ронять последний рубеж
        log.warning("alert.unreachable — Telegram недоступен, сбой виден только в логах")


def main() -> int:
    load_dotenv()
    config = load_config()
    configure_logging(config)

    stop = threading.Event()

    def _on_signal(signum: int, frame: FrameType | None) -> None:
        log.info("bot.stop signal=%d", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    conn = connect(config.db_path)
    try:
        init_db(conn)
        notifier = Notifier(config)
        scheduler = Scheduler(
            conn,
            config,
            TokenManager(conn, config),
            LinkedInClient(config),
            notifier,
        )
        log.info(
            "bot.start interval_minutes=%s orgs=%d",
            config.poll_interval_minutes, len(config.linkedin_org_urns),
        )
        run_loop(
            scheduler,
            notifier,
            stop,
            interval_seconds=config.poll_interval_minutes * 60,
            alert_threshold=config.failure_alert_threshold,
        )
    finally:
        conn.close()
    log.info("bot.stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
