"""Загрузка и валидация конфигурации из окружения (.env).

Fail fast: при любой некорректности собираем все ошибки и падаем один раз с понятным
сообщением, не запуская основной цикл.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ORG_URN_PREFIX = "urn:li:organization:"
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(ValueError):
    """Конфигурация некорректна или неполна."""


@dataclass(frozen=True, slots=True)
class Config:
    linkedin_client_id: str
    linkedin_client_secret: str
    linkedin_org_urns: tuple[str, ...]
    linkedin_api_version: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_minutes: int
    poll_overlap_minutes: int
    failure_alert_threshold: int
    refresh_token_warning_days: int
    db_path: str
    heartbeat_path: str
    timezone: str
    log_level: str


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Собрать и провалидировать конфиг из mapping (по умолчанию — os.environ)."""
    env = os.environ if env is None else env
    errors: list[str] = []
    config = Config(
        linkedin_client_id=_required(env, errors, "LINKEDIN_CLIENT_ID"),
        linkedin_client_secret=_required(env, errors, "LINKEDIN_CLIENT_SECRET"),
        linkedin_org_urns=_org_urns(env, errors),
        linkedin_api_version=_api_version(env, errors),
        telegram_bot_token=_required(env, errors, "TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required(env, errors, "TELEGRAM_CHAT_ID"),
        poll_interval_minutes=_bounded_int(env, errors, "POLL_INTERVAL_MINUTES", 15),
        poll_overlap_minutes=_bounded_int(
            env, errors, "POLL_OVERLAP_MINUTES", 5, allow_zero=True
        ),
        failure_alert_threshold=_bounded_int(env, errors, "FAILURE_ALERT_THRESHOLD", 3),
        refresh_token_warning_days=_bounded_int(
            env, errors, "REFRESH_TOKEN_WARNING_DAYS", 14
        ),
        db_path=(env.get("DB_PATH") or "/data/bot.db").strip(),
        heartbeat_path=(env.get("HEARTBEAT_PATH") or "/tmp/bot-heartbeat").strip(),
        timezone=_timezone(env, errors),
        log_level=_log_level(env, errors),
    )
    if errors:
        raise ConfigError("Ошибки конфигурации:\n- " + "\n- ".join(errors))
    return config


def _required(env: Mapping[str, str], errors: list[str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        errors.append(f"{key} обязателен и не должен быть пустым")
    return value


def _bounded_int(
    env: Mapping[str, str], errors: list[str], key: str, default: int,
    *, allow_zero: bool = False,
) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{key} должен быть целым числом, получено: {raw!r}")
        return default
    if value < 0 or (value == 0 and not allow_zero):
        bound = ">= 0" if allow_zero else "> 0"
        errors.append(f"{key} должен быть {bound}, получено: {value}")
    return value


def _org_urns(env: Mapping[str, str], errors: list[str]) -> tuple[str, ...]:
    raw = _required(env, errors, "LINKEDIN_ORG_URNS")
    if not raw:
        return ()
    parts = [p.strip() for p in raw.split(",")]
    if any(not p for p in parts):  # fail fast: пустой сегмент — вероятная опечатка
        errors.append(f"LINKEDIN_ORG_URNS содержит пустой элемент: {raw!r}")
    urns = tuple(p for p in parts if p)
    for urn in urns:
        if not urn.startswith(ORG_URN_PREFIX):
            errors.append(f"org URN должен начинаться с {ORG_URN_PREFIX!r}: {urn!r}")
    return urns


def _api_version(env: Mapping[str, str], errors: list[str]) -> str:
    value = (env.get("LINKEDIN_API_VERSION") or "202606").strip()
    valid = value.isdigit() and len(value) == 6 and 1 <= int(value[4:6]) <= 12
    if not valid:
        errors.append(
            f"LINKEDIN_API_VERSION должен быть в формате YYYYMM, получено: {value!r}"
        )
    return value


def _timezone(env: Mapping[str, str], errors: list[str]) -> str:
    value = (env.get("TIMEZONE") or "Europe/Kyiv").strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"TIMEZONE неизвестна: {value!r}")
    return value


def _log_level(env: Mapping[str, str], errors: list[str]) -> str:
    value = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    if value not in _LOG_LEVELS:
        errors.append(
            f"LOG_LEVEL должен быть DEBUG/INFO/WARNING/ERROR/CRITICAL, получено: {value!r}"
        )
    return value
