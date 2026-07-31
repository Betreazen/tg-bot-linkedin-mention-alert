from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src import cursor, health
from src.config import load_config
from src.errors import AuthError, PermanentError, RateLimitError, TransientError
from src.models import Notification
from src.scheduler import Scheduler

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
ORG = "urn:li:organization:123"


def _notif(nid: str, act: str = "urn:li:activity:1") -> Notification:
    return Notification(notification_id=nid, org_urn=ORG, activity_urn=act, detected_at=NOW)


class FakeTokenManager:
    def __init__(self, *, token="AT", days_left=100, error=None, refreshed_token=None):
        self._token = token
        self._days = days_left
        self._error = error
        self._refreshed = refreshed_token
        self.force_refresh_calls = 0

    def get_valid_access_token(self):
        if self._error is not None:
            raise self._error
        return self._token

    def force_refresh(self):
        self.force_refresh_calls += 1
        if self._refreshed is None:
            raise PermanentError("refresh не удался")
        return self._refreshed

    def refresh_token_days_left(self):
        return self._days


class FakeClient:
    def __init__(self, *, mentions=None, error=None):
        self._mentions = mentions or {}
        self._error = error
        self.calls: list[tuple[str, datetime, datetime]] = []

    def fetch_mentions(self, access_token, org_urn, since, until, *, now=None):
        self.calls.append((org_urn, since, until))
        if self._error is not None:
            raise self._error
        return list(self._mentions.get(org_urn, []))


class FakeNotifier:
    def __init__(self, *, fail_service=False, fail_warning=False):
        self.mentions = []
        self.service = []
        self.warnings = []
        self.service_attempts = 0
        self.warning_attempts = 0
        self._fail_service = fail_service
        self._fail_warning = fail_warning

    def send_mention(self, notification):
        self.mentions.append(notification)

    def send_service_alert(self, reason, last_success):
        self.service_attempts += 1
        if self._fail_service:
            raise PermanentError("telegram недоступен")
        self.service.append((reason, last_success))

    def send_refresh_warning(self, days):
        self.warning_attempts += 1
        if self._fail_warning:
            raise PermanentError("telegram недоступен")
        self.warnings.append(days)


def _scheduler(db_conn, *, token_manager, client, notifier, env):
    return Scheduler(
        db_conn, load_config(env), token_manager, client, notifier, now_fn=lambda: NOW
    )


def test_happy_cycle_sends_and_records(db_conn, valid_env):
    notifier = FakeNotifier()
    client = FakeClient(mentions={ORG: [_notif("n1"), _notif("n2", "urn:li:activity:2")]})
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=client,
                     notifier=notifier, env=valid_env)

    sch.run_cycle()

    assert [n.notification_id for n in notifier.mentions] == ["n1", "n2"]
    assert cursor.get_cursor(db_conn, ORG) == NOW
    h = health.get(db_conn)
    assert h.consecutive_failures == 0
    assert h.last_successful_poll == NOW


def test_dedup_across_cycles(db_conn, valid_env):
    notifier = FakeNotifier()
    client = FakeClient(mentions={ORG: [_notif("n1")]})
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=client,
                     notifier=notifier, env=valid_env)

    sch.run_cycle()
    sch.run_cycle()

    assert len(notifier.mentions) == 1  # второй цикл ничего не шлёт


def test_token_failure_alerts_only_at_threshold(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG  # threshold=3 по умолчанию
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=PermanentError("refresh отозван")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )

    sch.run_cycle()
    sch.run_cycle()
    assert notifier.service == []  # ещё не порог
    sch.run_cycle()
    assert len(notifier.service) == 1
    assert "токен" in notifier.service[0][0]


def test_org_fetch_failure_does_not_advance_cursor(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(),
        client=FakeClient(error=TransientError("5xx")),
        notifier=notifier,
        env=valid_env,
    )

    sch.run_cycle()

    assert cursor.get_cursor(db_conn, ORG) is None
    assert health.get(db_conn).consecutive_failures == 1


def test_partial_failure_sends_ok_org_and_registers_failure(db_conn, valid_env):
    org_ok = "urn:li:organization:1"
    org_bad = "urn:li:organization:2"
    valid_env["LINKEDIN_ORG_URNS"] = f"{org_ok},{org_bad}"

    class SplitClient:
        def fetch_mentions(self, access_token, org_urn, since, until, *, now=None):
            if org_urn == org_bad:
                raise TransientError("boom")
            return [Notification("ok1", org_ok, "urn:li:activity:1", NOW)]

    notifier = FakeNotifier()
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=SplitClient(),
                     notifier=notifier, env=valid_env)

    sch.run_cycle()

    assert [n.notification_id for n in notifier.mentions] == ["ok1"]
    assert cursor.get_cursor(db_conn, org_ok) == NOW      # успешный org — курсор двинут
    assert cursor.get_cursor(db_conn, org_bad) is None    # сбойный — нет
    assert health.get(db_conn).consecutive_failures == 1


def test_refresh_warning_sent_once_per_day(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(days_left=10),  # <= 14
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )

    sch.run_cycle()
    sch.run_cycle()  # тот же день → без повтора

    assert notifier.warnings == [10]


def test_no_refresh_warning_when_far(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(days_left=100),
                     client=FakeClient(), notifier=notifier, env=valid_env)
    sch.run_cycle()
    assert notifier.warnings == []


def test_no_refresh_warning_when_days_none(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(days_left=None),
                     client=FakeClient(), notifier=notifier, env=valid_env)
    sch.run_cycle()
    assert notifier.warnings == []


def test_rate_limited_token_not_counted_as_failure_but_alerted(db_conn, valid_env):
    """429 не в порог сбоев (SPEC §10), но затяжная серия 429 сама алертится — H4."""
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=RateLimitError("429")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(5):
        sch.run_cycle()
    assert health.get(db_conn).consecutive_failures == 0  # 429 не считается сбоем
    assert health.get(db_conn).consecutive_rate_limited == 5
    assert len(notifier.service) == 1  # порог 3 + суточный троттлинг → ровно один
    assert "rate limit" in notifier.service[0][0]


def test_rate_limited_org_not_counted_and_no_cursor(db_conn, valid_env):
    notifier = FakeNotifier()
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(),
        client=FakeClient(error=RateLimitError("429")),
        notifier=notifier,
        env=valid_env,
    )
    sch.run_cycle()
    assert health.get(db_conn).consecutive_failures == 0
    assert cursor.get_cursor(db_conn, ORG) is None


def test_service_alert_delivery_failure_retries_next_cycle(db_conn, valid_env):
    """Неудачная доставка алерта не теряет его: повтор в следующем цикле — H3."""
    notifier = FakeNotifier(fail_service=True)
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=PermanentError("boom")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(3):
        sch.run_cycle()  # порог достигнут, отправка падает — но цикл не роняется
    assert health.get(db_conn).consecutive_failures == 3
    assert notifier.service_attempts == 1  # попытка была, доставка не удалась

    sch.run_cycle()  # метка не поставлена → попытка повторяется
    assert notifier.service_attempts == 2

    notifier._fail_service = False
    sch.run_cycle()  # доставка удалась → метка поставлена
    assert len(notifier.service) == 1
    sch.run_cycle()  # в тот же день повторов больше нет
    assert notifier.service_attempts == 3


def test_refresh_warning_retries_after_failed_send(db_conn, valid_env):
    notifier = FakeNotifier(fail_warning=True)
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(days_left=5),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    sch.run_cycle()
    sch.run_cycle()
    # дата не помечается, пока отправка не удалась → повтор на следующем цикле
    assert notifier.warning_attempts == 2
    assert notifier.warnings == []


def test_refresh_warning_throttle_survives_restart(db_conn, valid_env):
    """Суточный троттлинг предупреждения хранится в БД, а не в памяти процесса."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    first = FakeNotifier()
    _scheduler(db_conn, token_manager=FakeTokenManager(days_left=5), client=FakeClient(),
               notifier=first, env=valid_env).run_cycle()
    assert first.warnings == [5]

    second = FakeNotifier()  # «рестарт контейнера»: новый Scheduler, та же БД
    _scheduler(db_conn, token_manager=FakeTokenManager(days_left=5), client=FakeClient(),
               notifier=second, env=valid_env).run_cycle()
    assert second.warnings == []


def test_heartbeat_file_written_every_cycle(db_conn, valid_env):
    """HEALTHCHECK читает файл: heartbeat пишется независимо от исхода цикла."""
    from pathlib import Path

    valid_env["LINKEDIN_ORG_URNS"] = ORG
    heartbeat = Path(valid_env["HEARTBEAT_PATH"])
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=PermanentError("сбой")),  # даже при сбое
        client=FakeClient(),
        notifier=FakeNotifier(),
        env=valid_env,
    )
    sch.run_cycle()
    assert heartbeat.read_text(encoding="utf-8") == NOW.isoformat()


def test_since_window_first_cycle_and_after_cursor(db_conn, valid_env):
    """Оконная математика _since — ядро «не потерять упоминание» (была не покрыта)."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    client = FakeClient()
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=client,
                     notifier=FakeNotifier(), env=valid_env)

    sch.run_cycle()
    # первый цикл: lookback = интервал (15) + оверлап (5)
    assert client.calls[0] == (ORG, NOW - timedelta(minutes=20), NOW)

    later = NOW + timedelta(minutes=15)
    sch._now = lambda: later  # второй тик
    sch.run_cycle()
    # дальше: от курсора (NOW) минус оверлап (5)
    assert client.calls[1] == (ORG, NOW - timedelta(minutes=5), later)


def test_unexpected_token_exception_counts_as_failure(db_conn, valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    notifier = FakeNotifier()
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=ValueError("не BotError")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    sch.run_cycle()
    assert health.get(db_conn).consecutive_failures == 1


def test_unexpected_exception_counts_as_failure(db_conn, valid_env):
    """Не-BotError (например, сбой SQLite) не обходит учёт сбоев и алертинг — C1."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    notifier = FakeNotifier()
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(),
        client=FakeClient(error=ValueError("совсем неожиданно")),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(3):
        sch.run_cycle()
    assert health.get(db_conn).consecutive_failures == 3
    assert len(notifier.service) == 1  # алертинг сработал, а не только docker logs
    assert "ValueError" in notifier.service[0][0]


def test_unexpected_exception_in_one_org_does_not_break_others(db_conn, valid_env):
    org_ok = "urn:li:organization:1"
    org_bad = "urn:li:organization:2"
    valid_env["LINKEDIN_ORG_URNS"] = f"{org_ok},{org_bad}"

    class SplitClient:
        def fetch_mentions(self, access_token, org_urn, since, until, *, now=None):
            if org_urn == org_bad:
                raise ValueError("не BotError")
            return [Notification("ok1", org_ok, "urn:li:activity:1", NOW)]

    notifier = FakeNotifier()
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=SplitClient(),
                     notifier=notifier, env=valid_env)
    sch.run_cycle()
    assert [n.notification_id for n in notifier.mentions] == ["ok1"]
    assert cursor.get_cursor(db_conn, org_ok) == NOW
    assert health.get(db_conn).consecutive_failures == 1


def test_service_alert_repeats_next_day_while_outage_continues(db_conn, valid_env):
    """Затяжной сбой: повторный алерт через сутки, а не одно сообщение навсегда — H3."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    notifier = FakeNotifier()
    sch = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=PermanentError("refresh мёртв")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(6):
        sch.run_cycle()
    assert len(notifier.service) == 1  # в первые сутки — один алерт

    sch._now = lambda: NOW + timedelta(days=1, minutes=1)
    sch.run_cycle()
    assert len(notifier.service) == 2  # сбой продолжается → суточный повтор


def test_alert_kind_change_not_throttled(db_conn, valid_env):
    """Смена вида сбоя (failure → rate_limit) алертится сразу, без суточной паузы."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    notifier = FakeNotifier()
    failing = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=PermanentError("5xx на токене")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(3):
        failing.run_cycle()
    assert len(notifier.service) == 1  # алерт о сбоях

    rate_limited = _scheduler(
        db_conn,
        token_manager=FakeTokenManager(error=RateLimitError("429")),
        client=FakeClient(),
        notifier=notifier,
        env=valid_env,
    )
    for _ in range(3):
        rate_limited.run_cycle()
    assert len(notifier.service) == 2  # причина сменилась → новый алерт в тот же день
    assert "rate limit" in notifier.service[1][0]


def test_auth_error_triggers_reactive_refresh_and_retry(db_conn, valid_env):
    """401/403 → форс-refresh + повтор запроса (SPEC §10) — H2."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG

    class AuthThenOkClient:
        def __init__(self):
            self.calls: list[str] = []

        def fetch_mentions(self, access_token, org_urn, since, until, *, now=None):
            self.calls.append(access_token)
            if access_token == "AT_STALE":
                raise AuthError("HTTP 401")
            return [_notif("n1")]

    tm = FakeTokenManager(token="AT_STALE", refreshed_token="AT_FRESH")
    client = AuthThenOkClient()
    notifier = FakeNotifier()
    sch = _scheduler(db_conn, token_manager=tm, client=client, notifier=notifier,
                     env=valid_env)
    sch.run_cycle()

    assert tm.force_refresh_calls == 1
    assert client.calls == ["AT_STALE", "AT_FRESH"]  # повтор с новым токеном
    assert [n.notification_id for n in notifier.mentions] == ["n1"]
    assert health.get(db_conn).consecutive_failures == 0
    assert cursor.get_cursor(db_conn, ORG) == NOW


def test_auth_error_with_failed_refresh_counts_as_failure(db_conn, valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = ORG
    tm = FakeTokenManager(refreshed_token=None)  # force_refresh падает
    notifier = FakeNotifier()
    sch = _scheduler(db_conn, token_manager=tm, client=FakeClient(error=AuthError("401")),
                     notifier=notifier, env=valid_env)
    sch.run_cycle()
    assert tm.force_refresh_calls == 1
    assert health.get(db_conn).consecutive_failures == 1
    assert cursor.get_cursor(db_conn, ORG) is None


def test_auth_refresh_attempted_once_per_cycle(db_conn, valid_env):
    org1 = "urn:li:organization:1"
    org2 = "urn:li:organization:2"
    valid_env["LINKEDIN_ORG_URNS"] = f"{org1},{org2}"
    tm = FakeTokenManager(refreshed_token="AT_FRESH")
    sch = _scheduler(db_conn, token_manager=tm, client=FakeClient(error=AuthError("401")),
                     notifier=FakeNotifier(), env=valid_env)
    sch.run_cycle()
    assert tm.force_refresh_calls == 1  # не по разу на каждый org


def test_permanent_send_failure_skips_item_not_batch(db_conn, valid_env):
    """Неотправляемое уведомление не блокирует остальные и не пишется в дедуп."""
    valid_env["LINKEDIN_ORG_URNS"] = ORG

    class PickyNotifier(FakeNotifier):
        def send_mention(self, notification):
            if notification.notification_id == "poison":
                raise PermanentError("Telegram: HTTP 400")
            super().send_mention(notification)

    client = FakeClient(mentions={ORG: [
        _notif("poison"), _notif("good", "urn:li:activity:2")
    ]})
    notifier = PickyNotifier()
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=client,
                     notifier=notifier, env=valid_env)
    sch.run_cycle()

    assert [n.notification_id for n in notifier.mentions] == ["good"]
    assert cursor.get_cursor(db_conn, ORG) == NOW  # org успешен
    assert health.get(db_conn).consecutive_failures == 0
    from src import dedup
    assert dedup.is_sent(db_conn, "good") is True
    assert dedup.is_sent(db_conn, "poison") is False  # не помечено отправленным


def test_transient_send_failure_aborts_batch_without_cursor(db_conn, valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = ORG

    class FlakyNotifier(FakeNotifier):
        def send_mention(self, notification):
            raise TransientError("сеть")

    client = FakeClient(mentions={ORG: [_notif("n1")]})
    sch = _scheduler(db_conn, token_manager=FakeTokenManager(), client=client,
                     notifier=FlakyNotifier(), env=valid_env)
    sch.run_cycle()
    assert cursor.get_cursor(db_conn, ORG) is None  # дошлём со следующего цикла
    assert health.get(db_conn).consecutive_failures == 1
