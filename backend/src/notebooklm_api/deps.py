"""Dependency injection for FastAPI routes."""

from collections.abc import AsyncGenerator

from notebooklm import NotebookLMClient

from .config import settings


async def get_client() -> AsyncGenerator[NotebookLMClient, None]:
    """Yield an authenticated NotebookLMClient for the request lifetime."""
    client = await NotebookLMClient.from_storage(
        path=settings.storage_path,
        timeout=settings.request_timeout,
    )
    async with client:
        yield client
