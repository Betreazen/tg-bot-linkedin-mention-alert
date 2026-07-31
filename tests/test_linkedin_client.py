from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from src.config import load_config
from src.errors import PermanentError
from src.linkedin_client import LinkedInClient, notifications_url, parse_notifications

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
UNTIL = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def test_notifications_url_restli_encoding():
    url = notifications_url("urn:li:organization:123", SINCE, UNTIL)
    assert "organizationalEntityNotifications" in url
    assert "q=criteria" in url
    assert "actions=List(SHARE_MENTION)" in url  # скобки не кодируются
    assert "organizationalEntity=urn%3Ali%3Aorganization%3A123" in url  # URN кодируется
    assert "timeRange=(start:" in url
    assert "start=0" in url and "count=50" in url  # пагинация


def test_notifications_url_paging_params():
    url = notifications_url("urn:li:organization:123", SINCE, UNTIL, start=100, count=50)
    assert "start=100" in url
    assert "count=50" in url


def test_parse_happy_path_matches_linkedin_shape():
    # Форма из офиц. доки: notificationId (long), generatedActivity/sourcePost, lastModifiedAt
    payload = {
        "elements": [
            {
                "notificationId": 4406044,
                "action": "SHARE_MENTION",
                "generatedActivity": "urn:li:share:6441333333905633322",
                "sourcePost": "urn:li:activity:92828282828282828828",
                "lastModifiedAt": 1690000000000,
            }
        ]
    }
    notifs = parse_notifications(payload, "urn:li:organization:12345", now=NOW)
    assert len(notifs) == 1
    n = notifs[0]
    assert n.notification_id == "4406044"  # числовой long → строка
    assert n.org_urn == "urn:li:organization:12345"
    assert n.activity_urn == "urn:li:activity:92828282828282828828"  # sourcePost в приоритете
    assert n.post_url == "https://www.linkedin.com/feed/update/urn:li:activity:92828282828282828828/"
    assert n.detected_at == datetime.fromtimestamp(1690000000000 / 1000, tz=UTC)


def test_parse_uses_source_post_for_link():
    payload = {"elements": [
        {"notificationId": 5, "sourcePost": "urn:li:activity:7", "action": "SHARE_MENTION"}
    ]}
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert notifs[0].activity_urn == "urn:li:activity:7"
    assert notifs[0].notification_id == "5"


def test_parse_falls_back_to_generated_activity_without_source_post():
    payload = {"elements": [
        {"notificationId": 6, "generatedActivity": "urn:li:share:99", "action": "SHARE_MENTION"}
    ]}
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert notifs[0].activity_urn == "urn:li:share:99"


def test_parse_falls_back_to_composite_key():
    payload = {"elements": [{"generatedActivity": "urn:li:activity:7", "action": "SHARE_MENTION"}]}
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert notifs[0].notification_id.startswith("sha1:")


def test_parse_uses_activity_field_fallback():
    payload = {"elements": [{"id": "x", "activity": "urn:li:activity:9"}]}
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert notifs[0].activity_urn == "urn:li:activity:9"


def test_parse_skips_non_share_mention():
    payload = {
        "elements": [
            {"id": "a", "action": "LIKE", "generatedActivity": "urn:li:activity:1"},
            {"id": "b", "action": "SHARE_MENTION", "generatedActivity": "urn:li:activity:2"},
        ]
    }
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert [n.notification_id for n in notifs] == ["b"]


def test_parse_missing_activity_urn_skipped_not_fatal():
    """Кривой элемент не роняет батч (иначе курсор замирает навсегда) — H1."""
    payload = {
        "elements": [
            {"id": "n1", "action": "SHARE_MENTION"},  # без URN — пропускается
            {"id": "n2", "action": "SHARE_MENTION", "sourcePost": "urn:li:activity:2"},
        ]
    }
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert [n.notification_id for n in notifs] == ["n2"]  # валидные доставлены


def test_parse_missing_event_time_uses_now():
    payload = {"elements": [{"notificationId": 9, "generatedActivity": "urn:li:activity:3"}]}
    notifs = parse_notifications(payload, "urn:li:organization:1", now=NOW)
    assert notifs[0].detected_at == NOW


def test_parse_elements_not_list_raises():
    with pytest.raises(PermanentError):
        parse_notifications({"elements": "nope"}, "urn:li:organization:1", now=NOW)


def test_parse_element_not_object_skipped():
    notifs = parse_notifications(
        {"elements": [42, {"id": "ok", "sourcePost": "urn:li:activity:1"}]},
        "urn:li:organization:1",
        now=NOW,
    )
    assert [n.notification_id for n in notifs] == ["ok"]


def test_composite_key_stable_across_calls():
    payload = {"elements": [{"generatedActivity": "urn:li:activity:7", "action": "SHARE_MENTION"}]}
    first = parse_notifications(payload, "urn:li:organization:1", now=NOW)[0]
    second = parse_notifications(payload, "urn:li:organization:1", now=NOW)[0]
    assert first.notification_id == second.notification_id


def test_fetch_mentions_sends_headers_and_parses(valid_env):
    captured: dict[str, str] = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization", "")
        captured["version"] = request.headers.get("LinkedIn-Version", "")
        captured["restli"] = request.headers.get("X-Restli-Protocol-Version", "")
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "notificationId": 4406044,
                        "action": "SHARE_MENTION",
                        "generatedActivity": "urn:li:share:6441333333905633322",
                        "lastModifiedAt": 1690000000000,
                    }
                ]
            },
        )

    client = LinkedInClient(load_config(valid_env), transport=httpx.MockTransport(handler))
    notifs = client.fetch_mentions("AT", "urn:li:organization:123", SINCE, UNTIL, now=NOW)

    assert [n.activity_urn for n in notifs] == ["urn:li:share:6441333333905633322"]
    assert captured["auth"] == "Bearer AT"
    assert captured["version"] == "202606"
    assert captured["restli"] == "2.0.0"
    assert "urn%3Ali%3Aorganization%3A123" in captured["url"]
    assert "SHARE_MENTION" in captured["url"]


def _element(i: int) -> dict:
    return {
        "notificationId": i,
        "action": "SHARE_MENTION",
        "sourcePost": f"urn:li:activity:{i}",
    }


def test_fetch_mentions_paginates_full_pages(valid_env):
    """Полная страница (PAGE_SIZE) → запрашивается следующая; всплеск не теряется."""
    from src.linkedin_client import PAGE_SIZE

    requested: list[str] = []

    def handler(request):
        requested.append(str(request.url))
        start = int(request.url.params["start"])
        if start == 0:
            return httpx.Response(200, json={"elements": [_element(i) for i in range(PAGE_SIZE)]})
        return httpx.Response(200, json={"elements": [_element(PAGE_SIZE)]})  # хвост

    client = LinkedInClient(load_config(valid_env), transport=httpx.MockTransport(handler))
    notifs = client.fetch_mentions("AT", "urn:li:organization:1", SINCE, UNTIL, now=NOW)

    assert len(requested) == 2
    assert f"start={PAGE_SIZE}" in requested[1]
    assert len(notifs) == PAGE_SIZE + 1


def test_fetch_mentions_single_partial_page_no_extra_requests(valid_env):
    requested: list[str] = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json={"elements": [_element(1)]})

    client = LinkedInClient(load_config(valid_env), transport=httpx.MockTransport(handler))
    client.fetch_mentions("AT", "urn:li:organization:1", SINCE, UNTIL, now=NOW)
    assert len(requested) == 1
