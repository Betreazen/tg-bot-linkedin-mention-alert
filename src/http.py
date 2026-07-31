"""HTTP-обвязка (порт smm-dashboard/connectors/http.py): единая классификация
ошибок для LinkedIn и Telegram.

Правила:
- 429 → RateLimitError с retry_after из заголовка (backoff, уважение quota);
- 5xx и сетевые ошибки/таймауты → TransientError;
- 401/403 → AuthError (повод для реактивного refresh токена, SPEC §10);
- прочие 4xx (нет прав, протух refresh, кривой запрос) → PermanentError;
- тело ошибки урезается, query из URL вырезается (там бывают токены).
"""

from __future__ import annotations

from typing import Any

import httpx

from src.errors import AuthError, PermanentError, RateLimitError, TransientError

#: суммарный таймаут запроса; редкому polling спешить некуда
TIMEOUT_S = 30.0
_ERROR_BODY_LIMIT = 500


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Клиент; transport подменяется в тестах (httpx.MockTransport)."""
    return httpx.Client(timeout=TIMEOUT_S, transport=transport)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Запрос с классификацией ошибок; ответ обязан быть JSON-объектом."""
    try:
        response = client.request(method, url, data=data, headers=headers)
    except httpx.HTTPError as exc:  # сеть, таймаут, DNS
        raise TransientError(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
    if response.status_code == 429:
        raise RateLimitError(_describe(method, response), retry_after=_retry_after(response))
    if response.status_code >= 500:
        raise TransientError(_describe(method, response))
    if response.status_code in (401, 403):
        raise AuthError(_describe(method, response))
    if response.status_code >= 400:
        raise PermanentError(_describe(method, response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise PermanentError(f"{method} {url}: ответ не JSON") from exc
    if not isinstance(payload, dict):
        raise PermanentError(
            f"{method} {url}: ожидается JSON-объект, а не {type(payload).__name__}"
        )
    return payload


def get_json(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return request_json(client, "GET", url, headers=headers)


def _describe(method: str, response: httpx.Response) -> str:
    body = response.text[:_ERROR_BODY_LIMIT]
    url = response.request.url.copy_with(query=None)  # без query: там бывают токены
    return f"{method} {url}: HTTP {response.status_code}: {body}"


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("Retry-After")
    if raw is None or not raw.isdigit():
        return None
    return int(raw)
