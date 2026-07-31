"""Docker HEALTHCHECK: жив ли цикл опроса (`python -m src.healthcheck`).

Критерий: mtime heartbeat-файла (пишется планировщиком каждый цикл) не старше двух
интервалов опроса. Файл вместо чтения SQLite сознательно: второе соединение к БД на
bind mount (Docker Desktop, gRPC-FUSE) нестабильно, а heartbeat-файлу нужна только ФС.
Отсутствие файла = ещё не было ни одного цикла (grace на старт, покрывается
--start-period). Любая ошибка проверки — unhealthy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.config import load_config

#: во сколько интервалов опроса должен уложиться очередной heartbeat
STALE_CYCLES = 2


def check() -> str | None:
    """None — здоров; иначе причина unhealthy."""
    config = load_config()
    path = Path(config.heartbeat_path)
    if not path.exists():
        return None  # ещё не было ни одного цикла — грейс на старт
    age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    limit = timedelta(minutes=config.poll_interval_minutes * STALE_CYCLES)
    if age > limit:
        return f"цикл молчит {age} (порог {limit})"
    return None


def main() -> int:
    load_dotenv()
    try:
        reason = check()
    except Exception as exc:  # noqa: BLE001 — любой сбой проверки = unhealthy
        print(f"healthcheck: {type(exc).__name__}: {exc}")
        return 1
    if reason is not None:
        print(f"healthcheck: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
