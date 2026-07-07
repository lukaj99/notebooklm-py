"""Regression test for the /chat/history conversation_id bug.

The route resolved a conversation_id (conv_id) to report back to the caller,
but was passing the original, possibly-None conversation_id straight
through to client.chat.get_history() instead — meaning the returned
"conversation_id" field could silently describe a different conversation
than the one the history was actually fetched for.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_history_uses_resolved_conversation_id_when_none_provided(
    client: TestClient, api_key: str, fake_client: Any
) -> None:
    response = client.get(
        "/api/v1/notebooks/nb-1/chat/history",
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == "resolved-conversation-id"
    # The bug: get_history was called with the raw (None) conversation_id
    # instead of the resolved one.
    assert fake_client.chat.get_history_calls == ["resolved-conversation-id"]


def test_history_passes_through_explicit_conversation_id(
    client: TestClient, api_key: str, fake_client: Any
) -> None:
    response = client.get(
        "/api/v1/notebooks/nb-1/chat/history",
        params={"conversation_id": "explicit-conv-id"},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] == "explicit-conv-id"
    assert fake_client.chat.get_history_calls == ["explicit-conv-id"]
