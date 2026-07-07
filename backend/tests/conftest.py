"""Shared fixtures for notebooklm_api tests.

FakeNotebookLMClient stands in for the real notebooklm-py client so tests
exercise routing/auth/serialization behavior without touching a live Google
account.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notebooklm_api.config import settings
from notebooklm_api.deps import get_client
from notebooklm_api.main import create_app


class FakeChat:
    def __init__(self) -> None:
        self.get_history_calls: list[str | None] = []

    async def get_conversation_id(self, notebook_id: str) -> str:
        return "resolved-conversation-id"

    async def get_history(
        self, notebook_id: str, limit: int = 100, conversation_id: str | None = None
    ) -> list[tuple[str, str]]:
        self.get_history_calls.append(conversation_id)
        return [("q1", "a1")]

    async def ask(
        self,
        notebook_id: str,
        question: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ):
        class _Result:
            answer = "the answer"
            conversation_id = "resolved-conversation-id"
            turn_number = 1
            is_follow_up = False
            references: list = []

        return _Result()


class FakeNotebooks:
    async def list(self) -> list:
        return []


class FakeNotebookLMClient:
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.notebooks = FakeNotebooks()


@pytest.fixture
def fake_client() -> FakeNotebookLMClient:
    return FakeNotebookLMClient()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "test-api-key-0123456789"
    monkeypatch.setattr(settings, "api_key", key)
    return key


@pytest.fixture
def app(fake_client: FakeNotebookLMClient) -> FastAPI:
    application = create_app()

    async def _override_get_client() -> AsyncGenerator[FakeNotebookLMClient, None]:
        yield fake_client

    application.dependency_overrides[get_client] = _override_get_client
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)
