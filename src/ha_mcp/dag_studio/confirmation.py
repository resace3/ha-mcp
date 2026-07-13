"""Single-use confirmation tokens for high-impact DAG operations."""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class _Confirmation:
    action: str
    document_id: str
    revision: int
    expires_at: datetime


class ConfirmationStore:
    """In-memory, short-lived and single-use confirmation token store."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[str, _Confirmation] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, action: str, document_id: str, revision: int) -> str:
        token = secrets.token_urlsafe(32)
        item = _Confirmation(
            action=action,
            document_id=document_id,
            revision=revision,
            expires_at=datetime.now(UTC) + self._ttl,
        )
        with self._lock:
            self._items[self._digest(token)] = item
        return token

    def consume(self, token: str, action: str, document_id: str, revision: int) -> None:
        digest = self._digest(token)
        with self._lock:
            item = self._items.pop(digest, None)
        if item is None:
            raise ValueError("invalid or already-used confirmation token")
        if item.expires_at < datetime.now(UTC):
            raise ValueError("confirmation token expired")
        if (item.action, item.document_id, item.revision) != (
            action,
            document_id,
            revision,
        ):
            raise ValueError("confirmation token does not match this operation")
