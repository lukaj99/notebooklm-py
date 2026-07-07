"""Tests for the require_api_key dependency wired into every /api/v1 router.

This backend has no per-request authorization model of its own (one owner,
one Google account) — require_api_key is the entire trust boundary once a
request reaches the process. It was previously deployed publicly via a
Cloudflare tunnel with no application-level auth check at all.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from notebooklm_api.config import settings


def test_health_is_public_even_without_api_key(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_protected_route_rejects_missing_api_key(client: TestClient, api_key: str) -> None:
    response = client.get("/api/v1/notebooks")
    assert response.status_code == 401


def test_protected_route_rejects_wrong_api_key(client: TestClient, api_key: str) -> None:
    response = client.get("/api/v1/notebooks", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_protected_route_accepts_correct_api_key(client: TestClient, api_key: str) -> None:
    response = client.get("/api/v1/notebooks", headers={"X-API-Key": api_key})
    assert response.status_code == 200


def test_protected_route_fails_closed_when_key_unconfigured(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "api_key", None)
    response = client.get("/api/v1/notebooks")
    assert response.status_code == 503
