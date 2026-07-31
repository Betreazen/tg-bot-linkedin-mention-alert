"""Доменные модели (иммутабельные)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Пара OAuth-токенов LinkedIn и сроки их жизни."""

    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Notification:
    """Упоминание компании (событие SHARE_MENTION)."""

    notification_id: str
    org_urn: str
    activity_urn: str
    detected_at: datetime

    @property
    def post_url(self) -> str:
        """Ссылка на пост LinkedIn по URN активности."""
        return f"https://www.linkedin.com/feed/update/{self.activity_urn}/"
