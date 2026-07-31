from __future__ import annotations

import httpx
import pytest

from src.errors import AuthError, PermanentError, RateLimitError, TransientError
from src.http import get_json, make_client


def _client(handler):
    return make_client(httpx.MockTransport(handler))


def test_get_json_ok():
    with _client(lambda r: httpx.Response(200, json={"ok": True})) as c:
        assert get_json(c, "https://api.example/x") == {"ok": True}


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses(status):
    with _client(lambda r: httpx.Response(status, text="busy")) as c:
        with pytest.raises(TransientError):
            get_json(c, "https://api.example/x")


def test_429_is_rate_limit_error():
    with _client(lambda r: httpx.Response(429, text="slow down")) as c:
        with pytest.raises(RateLimitError):  # подкласс TransientError
            get_json(c, "https://api.example/x")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_statuses(status):
    with _client(lambda r: httpx.Response(status, text="nope")) as c:
        with pytest.raises(PermanentError):
            get_json(c, "https://api.example/x")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses_are_auth_error(status):
    """401/403 различимы — Scheduler реагирует реактивным refresh (SPEC §10)."""
    with _client(lambda r: httpx.Response(status, text="denied")) as c:
        with pytest.raises(AuthError):
            get_json(c, "https://api.example/x")


def test_rate_limit_carries_retry_after():
    with _client(
        lambda r: httpx.Response(429, text="slow", headers={"Retry-After": "17"})
    ) as c:
        with pytest.raises(RateLimitError) as exc:
            get_json(c, "https://api.example/x")
    assert exc.value.retry_after == 17


def test_rate_limit_without_retry_after_header():
    with _client(lambda r: httpx.Response(429, text="slow")) as c:
        with pytest.raises(RateLimitError) as exc:
            get_json(c, "https://api.example/x")
    assert exc.value.retry_after is None


def test_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("boom")

    with _client(handler) as c:
        with pytest.raises(TransientError):
            get_json(c, "https://api.example/x")


def test_non_json_is_permanent():
    with _client(lambda r: httpx.Response(200, text="<html>nope</html>")) as c:
        with pytest.raises(PermanentError):
            get_json(c, "https://api.example/x")


def test_non_object_json_is_permanent():
    with _client(lambda r: httpx.Response(200, json=[1, 2, 3])) as c:
        with pytest.raises(PermanentError):
            get_json(c, "https://api.example/x")


def test_error_message_strips_query_secrets():
    with _client(lambda r: httpx.Response(400, text="bad")) as c:
        with pytest.raises(PermanentError) as exc:
            get_json(c, "https://api.example/x?access_token=SECRET123")
    assert "SECRET123" not in str(exc.value)
