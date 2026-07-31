from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from src.models import Notification, TokenSet


def test_notification_post_url():
    n = Notification(
        notification_id="n1",
        org_urn="urn:li:organization:1",
        activity_urn="urn:li:activity:42",
        detected_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    assert n.post_url == "https://www.linkedin.com/feed/update/urn:li:activity:42/"


def test_tokenset_is_frozen():
    t = TokenSet(
        access_token="a",
        refresh_token="r",
        access_token_expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        refresh_token_expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(FrozenInstanceError):
        t.access_token = "x"  # type: ignore[misc]
