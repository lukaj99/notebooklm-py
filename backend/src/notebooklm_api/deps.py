"""Dependency injection for FastAPI routes."""

import hmac
import logging
from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException, status
from notebooklm import NotebookLMClient

from .config import settings

logger = logging.getLogger(__name__)

_warned_missing_key = False


async def get_client() -> AsyncGenerator[NotebookLMClient, None]:
    """Yield an authenticated NotebookLMClient for the request lifetime."""
    client = await NotebookLMClient.from_storage(
        path=settings.storage_path,
        timeout=settings.request_timeout,
    )
    async with client:
        yield client


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Require a valid X-API-Key header on every protected route.

    This backend drives one personal Google account with no per-request
    authorization model of its own (there is no notion of "whose"
    notebook), so a single shared-secret gate is the whole trust boundary
    once a request reaches this process. It fails closed: if
    NOTEBOOKLM_API_API_KEY isn't configured, every request is rejected
    rather than silently served without authentication (this API has
    previously been deployed publicly via a Cloudflare tunnel with no
    application-level check at all).
    """

    if not settings.api_key:
        global _warned_missing_key
        if not _warned_missing_key:
            logger.error(
                "NOTEBOOKLM_API_API_KEY is not set; rejecting all requests. "
                "Set NOTEBOOKLM_API_API_KEY to a long random value to enable this API."
            )
            _warned_missing_key = True
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key not configured",
        )

    if x_api_key is None or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
