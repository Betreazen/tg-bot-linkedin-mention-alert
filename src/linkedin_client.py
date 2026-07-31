"""LinkedIn Client (SPEC §3.2, §8.2): запрос упоминаний организации.

Обёртка над `GET /rest/organizationalEntityNotifications` с фильтром
`actions=List(SHARE_MENTION)`, `timeRange` и пагинацией `start`/`count`. URL собирается
вручную по конвенции Restli 2.0 (порт из smm-dashboard): скобочные параметры НЕ
кодируются, URN — кодируется `quote(urn, safe='')`.

Устойчивость парсинга: один кривой элемент НЕ роняет весь батч (иначе курсор org
замирает и алерты по org блокируются навсегда) — элемент пропускается с WARNING,
остальные доставляются.

Форма ответа подтверждена офиц. докой (Organization Social Action Notifications,
li-lms-2026-06) и живым прогоном (Фаза 0): `elements[]` с `notificationId` (long),
`sourcePost` (activity URN, приоритет для ссылки), `generatedActivity` (фолбэк),
`lastModifiedAt` (epoch-мс).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from src.config import Config
from src.errors import PermanentError
from src.http import get_json, make_client
from src.models import Notification

log = logging.getLogger(__name__)

REST_URL = "https://api.linkedin.com/rest"
NOTIFICATIONS_ENDPOINT = "organizationalEntityNotifications"
SHARE_MENTION = "SHARE_MENTION"
#: размер страницы finder'а; окно в 15 минут почти всегда умещается в одну
PAGE_SIZE = 50
#: предохранитель от бесконечной пагинации при неожиданном поведении API
MAX_PAGES = 20


def notifications_url(
    org_urn: str, since: datetime, until: datetime, *, start: int = 0, count: int = PAGE_SIZE
) -> str:
    """URL уведомлений в синтаксисе Restli 2.0 (скобки не кодируются, URN — да)."""
    return (
        f"{REST_URL}/{NOTIFICATIONS_ENDPOINT}"
        f"?q=criteria"
        f"&actions=List({SHARE_MENTION})"
        f"&organizationalEntity={quote(org_urn, safe='')}"
        f"&timeRange=(start:{_epoch_ms(since)},end:{_epoch_ms(until)})"
        f"&start={start}&count={count}"
    )


class LinkedInClient:
    def __init__(self, config: Config, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._transport = transport

    def fetch_mentions(
        self,
        access_token: str,
        org_urn: str,
        since: datetime,
        until: datetime,
        *,
        now: datetime | None = None,
    ) -> list[Notification]:
        """Упоминания SHARE_MENTION одной организации за окно [since, until]."""
        now = now or datetime.now(UTC)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": self._config.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        result: list[Notification] = []
        with make_client(self._transport) as client:
            for page in range(MAX_PAGES):
                url = notifications_url(
                    org_urn, since, until, start=page * PAGE_SIZE, count=PAGE_SIZE
                )
                payload = get_json(client, url, headers=headers)
                result.extend(parse_notifications(payload, org_urn, now=now))
                if len(payload["elements"]) < PAGE_SIZE:  # elements проверен в parse
                    break
            else:
                # не молчим об обрезанной выборке (окно перечитается со следующим циклом)
                log.warning(
                    "fetch.page_cap org=%s pages=%d — выборка может быть неполной",
                    org_urn, MAX_PAGES,
                )
        return result


def parse_notifications(
    payload: Mapping[str, object], org_urn: str, *, now: datetime
) -> list[Notification]:
    """Разбор страницы ответа; кривые элементы пропускаются с WARNING, не роняя батч."""
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise PermanentError("ответ LinkedIn: 'elements' не список")
    result: list[Notification] = []
    for i, element in enumerate(elements):
        if not isinstance(element, dict):
            log.warning("parse.element_skipped org=%s where=elements[%d]: не объект", org_urn, i)
            continue
        try:
            notification = _parse_element(element, org_urn, now=now, where=f"elements[{i}]")
        except PermanentError as exc:
            log.warning("parse.element_skipped org=%s error=%s", org_urn, exc)
            continue
        if notification is not None:
            result.append(notification)
    return result


def _parse_element(
    element: Mapping[str, object], org_urn: str, *, now: datetime, where: str
) -> Notification | None:
    action = element.get("action")
    if isinstance(action, str) and action != SHARE_MENTION:
        return None  # защитно: сервер уже фильтрует, но не доверяем вслепую

    # Ссылка на пост-упоминание: sourcePost — activity-URN, каноничный permalink LinkedIn
    # (/feed/update/urn:li:activity:.../). Подтверждено живым ответом (Фаза 0): активити-
    # форма надёжнее, чем share/ugcPost из generatedActivity (он — фолбэк).
    activity_urn = (
        element.get("sourcePost")
        or element.get("generatedActivity")
        or element.get("activity")
    )
    if not isinstance(activity_urn, str) or not activity_urn:
        raise PermanentError(f"{where}: нет URN активности (sourcePost/generatedActivity)")

    return Notification(
        notification_id=_dedup_key(element, org_urn, activity_urn, action),
        org_urn=org_urn,
        activity_urn=activity_urn,
        detected_at=_event_time(element.get("lastModifiedAt"), now, where=where),
    )


def _dedup_key(
    element: Mapping[str, object], org_urn: str, activity_urn: str, action: object
) -> str:
    """Стабильный id уведомления, если он есть; иначе составной ключ (SPEC §8.4).

    LinkedIn отдаёт `notificationId` как long (число) — приводим к строке.
    """
    for field in ("notificationId", "id"):
        value = element.get(field)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    # составной ключ слабее стабильного id (возможны коллизии) — оставляем след
    log.warning("dedup.composite_key_fallback org=%s activity=%s", org_urn, activity_urn)
    raw = f"{org_urn}|{activity_urn}|{action if isinstance(action, str) else ''}"
    return "sha1:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _event_time(raw: object, now: datetime, *, where: str) -> datetime:
    """lastModifiedAt (epoch-мс) → UTC; отсутствие/кривизна → время обнаружения (now)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        if raw is not None:  # отсутствие поля — норма, кривой тип — повод посмотреть
            log.warning("parse.event_time_fallback where=%s value=%r", where, raw)
        return now
    return datetime.fromtimestamp(raw / 1000, tz=UTC)


def _epoch_ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)
