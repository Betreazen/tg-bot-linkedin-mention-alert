"""Scheduler (SPEC §3.2, §5, §10): один цикл опроса всех org URN.

Инварианты:
- частичный сбой по одному org НЕ роняет остальные — включая сбои SQLite и любые
  неожиданные исключения (всё идёт в учёт сбоев, а не мимо алертинга);
- курсор org двигается только после его успешного опроса;
- 401/403 → один реактивный refresh токена за цикл + повтор запроса (SPEC §10);
- служебный алерт: при достижении порога подряд идущих сбоев, с повтором раз в сутки,
  пока сбой продолжается; неудачная доставка алерта повторяется в следующем цикле;
- подряд идущие rate-limited циклы считаются отдельно и тоже алертятся по порогу;
- отправка упоминания → запись в дедуп (at-least-once; дубли режет UNIQUE); перманентно
  неотправляемое уведомление пропускается, не блокируя остальные.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src import cursor, dedup, health
from src.config import Config
from src.errors import AuthError, BotError, PermanentError, RateLimitError, TransientError
from src.linkedin_client import LinkedInClient
from src.notifier import Notifier
from src.token_manager import TokenManager

log = logging.getLogger(__name__)

#: пока сбой продолжается, служебный алерт повторяется не чаще этого интервала
ALERT_REPEAT_INTERVAL = timedelta(days=1)


class Scheduler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        token_manager: TokenManager,
        linkedin_client: LinkedInClient,
        notifier: Notifier,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._config = config
        self._token_manager = token_manager
        self._client = linkedin_client
        self._notifier = notifier
        self._now = now_fn or (lambda: datetime.now(UTC))

    def run_cycle(self) -> None:
        now = self._now()
        health.record_cycle(self._conn, now)
        self._touch_heartbeat(now)  # файл для HEALTHCHECK: без второго соединения к SQLite
        try:
            access_token = self._token_manager.get_valid_access_token()
        except RateLimitError as exc:
            log.warning("token.rate_limited error=%s", exc)  # не в порог сбоев (SPEC §10)
            self._register_rate_limited(now)
            return
        except BotError as exc:
            self._register_failure(f"токен: {exc}", now)
            return
        except Exception as exc:  # noqa: BLE001 — неожиданное тоже должно алертить
            log.exception("token.unexpected_error")
            self._register_failure(f"токен: {type(exc).__name__}: {exc}", now)
            return

        self._maybe_warn_refresh(now)

        errors, rate_limited = self._poll_all(access_token, now)
        if errors:
            self._register_failure("; ".join(errors), now)
        elif rate_limited:
            self._register_rate_limited(now)
        else:
            health.record_success(self._conn, now)
            dedup.prune(self._conn, now)
            log.info("cycle.ok orgs=%d", len(self._config.linkedin_org_urns))

    def _poll_all(self, access_token: str, now: datetime) -> tuple[list[str], int]:
        """Опрос всех org; один реактивный refresh на цикл при 401/403 (SPEC §10)."""
        errors: list[str] = []
        rate_limited = 0
        auth_retried = False
        for org_urn in self._config.linkedin_org_urns:
            try:
                self._poll_org(access_token, org_urn, now)
            except AuthError as exc:
                refreshed = None if auth_retried else self._reactive_refresh()
                auth_retried = True
                if refreshed is None:
                    errors.append(f"{org_urn}: {exc}")
                    log.warning("poll.org_failed org=%s error=%s", org_urn, exc)
                    continue
                access_token = refreshed
                self._retry_org(access_token, org_urn, now, errors)
            except RateLimitError:
                rate_limited += 1
                log.warning("poll.rate_limited org=%s", org_urn)  # не считаем в порог
            except BotError as exc:
                errors.append(f"{org_urn}: {exc}")
                log.warning("poll.org_failed org=%s error=%s", org_urn, exc)
            except Exception as exc:  # noqa: BLE001 — не даём обойти учёт сбоев
                errors.append(f"{org_urn}: {type(exc).__name__}: {exc}")
                log.exception("poll.org_unexpected org=%s", org_urn)
        return errors, rate_limited

    def _retry_org(
        self, access_token: str, org_urn: str, now: datetime, errors: list[str]
    ) -> None:
        """Повтор опроса org после реактивного refresh; вторая неудача — обычный сбой."""
        try:
            self._poll_org(access_token, org_urn, now)
        except BotError as exc:
            errors.append(f"{org_urn}: {exc}")
            log.warning("poll.org_failed_after_refresh org=%s error=%s", org_urn, exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{org_urn}: {type(exc).__name__}: {exc}")
            log.exception("poll.org_unexpected org=%s", org_urn)

    def _reactive_refresh(self) -> str | None:
        """401/403 → форс-refresh (SPEC §10); неудача — None, org уйдёт в обычный сбой."""
        try:
            token = self._token_manager.force_refresh()
        except BotError as exc:
            log.warning("token.reactive_refresh_failed error=%s", exc)
            return None
        log.info("token.reactive_refresh_ok")
        return token

    def _poll_org(self, access_token: str, org_urn: str, now: datetime) -> None:
        since = self._since(org_urn, now)
        notifications = self._client.fetch_mentions(access_token, org_urn, since, now, now=now)
        sent = 0
        for notification in notifications:
            if dedup.is_sent(self._conn, notification.notification_id):
                continue
            try:
                self._notifier.send_mention(notification)
            except (RateLimitError, TransientError):
                raise  # временное: прервать батч, дошлём со следующего цикла (курсор стоит)
            except PermanentError as exc:
                # неотправляемое уведомление не должно блокировать остальные;
                # повторная попытка — пока упоминание в окне опроса
                log.warning(
                    "send.mention_failed id=%s error=%s",
                    notification.notification_id, exc,
                )
                continue
            dedup.record_sent(self._conn, notification, now)
            sent += 1
        cursor.set_cursor(self._conn, org_urn, now)  # только при успехе
        if sent:
            log.info("poll.sent org=%s count=%d", org_urn, sent)

    def _since(self, org_urn: str, now: datetime) -> datetime:
        last = cursor.get_cursor(self._conn, org_urn)
        base = last if last is not None else now - timedelta(minutes=self._config.poll_interval_minutes)
        return base - timedelta(minutes=self._config.poll_overlap_minutes)

    def _register_failure(self, reason: str, now: datetime) -> None:
        count = health.record_failure(self._conn)
        log.warning("cycle.failure count=%d reason=%s", count, reason)
        self._maybe_service_alert(count, reason, now, kind="failure")

    def _register_rate_limited(self, now: datetime) -> None:
        count = health.record_rate_limited(self._conn)
        log.info("cycle.rate_limited count=%d", count)  # ни успех, ни сбой (SPEC §10)
        self._maybe_service_alert(
            count, f"LinkedIn rate limit (429): {count} циклов подряд", now,
            kind="rate_limit",
        )

    def _maybe_service_alert(self, count: int, reason: str, now: datetime, *, kind: str) -> None:
        """Алерт при достижении порога; повтор раз в сутки, пока сбой продолжается.

        Троттлинг — по виду сбоя: смена failure↔rate_limit алертится сразу, иначе
        оператор до суток не узнал бы, что причина изменилась. Метка ставится только
        после успешной доставки — неудача повторит попытку, алерт не теряется.
        """
        if count < self._config.failure_alert_threshold:
            return
        state = health.get(self._conn)
        last_alert = state.last_service_alert_at
        same_kind = state.last_service_alert_kind == kind
        if last_alert is not None and same_kind and now - last_alert < ALERT_REPEAT_INTERVAL:
            return
        if self._safe_send(lambda: self._notifier.send_service_alert(
            reason, state.last_successful_poll
        )):
            health.set_service_alert_at(self._conn, now, kind)

    def _maybe_warn_refresh(self, now: datetime) -> None:
        days_left = self._token_manager.refresh_token_days_left()
        if days_left is None or days_left > self._config.refresh_token_warning_days:
            return
        today = now.date()
        if health.get(self._conn).last_refresh_warning_date == today:  # раз в сутки
            return
        # дату помечаем только после успешной отправки, иначе предупреждение потеряется
        if self._safe_send(lambda: self._notifier.send_refresh_warning(days_left)):
            health.set_refresh_warning_date(self._conn, today)

    def _touch_heartbeat(self, now: datetime) -> None:
        """Heartbeat-файл для Docker HEALTHCHECK.

        Второе соединение к SQLite на bind mount (Docker Desktop, gRPC-FUSE) нестабильно —
        поэтому проверка живости читает mtime файла, а не БД. Сбой записи не роняет цикл.
        """
        try:
            Path(self._config.heartbeat_path).write_text(now.isoformat(), encoding="utf-8")
        except OSError as exc:
            log.warning("heartbeat.write_failed path=%s error=%s",
                        self._config.heartbeat_path, exc)

    def _safe_send(self, send: Callable[[], None]) -> bool:
        """Доставка алерта не должна ронять цикл; ошибку логируем скрабленной."""
        try:
            send()
            return True
        except BotError as exc:
            log.warning("notify.failed error=%s", exc)
            return False
