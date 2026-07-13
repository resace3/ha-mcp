from __future__ import annotations

import time

import pytest

from ha_mcp.dag_studio.confirmation import ConfirmationStore


def test_confirmation_is_single_use_and_bound() -> None:
    store = ConfirmationStore(30)
    token = store.issue("approve", "doc", 3)
    store.consume(token, "approve", "doc", 3)
    with pytest.raises(ValueError, match="already-used"):
        store.consume(token, "approve", "doc", 3)


def test_confirmation_rejects_wrong_binding() -> None:
    store = ConfirmationStore(30)
    token = store.issue("delete", "doc", 3)
    with pytest.raises(ValueError, match="does not match"):
        store.consume(token, "delete", "other", 3)


def test_confirmation_expires() -> None:
    store = ConfirmationStore(0)
    token = store.issue("delete", "doc", 3)
    time.sleep(0.001)
    with pytest.raises(ValueError, match="expired"):
        store.consume(token, "delete", "doc", 3)
