from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from notebooklm_mcp.config import RemoteServerConfig
from notebooklm_mcp.oauth import FileBackedOAuthProvider
from notebooklm_mcp.remote import build_auth_settings
from notebooklm_mcp.server import create_mcp_server


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_remote_server_oauth_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "http://localhost:8006")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_STORE_PATH", str(tmp_path / "oauth-state.json"))

    config = RemoteServerConfig.from_env()
    provider = FileBackedOAuthProvider(config)
    mcp = create_mcp_server(
        host=config.host,
        port=config.port,
        auth_settings=build_auth_settings(config),
        auth_provider=provider,
        oauth_password=config.oauth_password,
    )

    with TestClient(mcp.streamable_http_app()) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert root.json()["oauth_enabled"] is True

        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        assert metadata.json()["authorization_endpoint"] == "http://localhost:8006/authorize"

        resource_metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource_metadata.status_code == 200
        assert resource_metadata.json()["resource"] == "http://localhost:8006/mcp"

        registration = client.post(
            "/register",
            json={
                "client_name": "Anthropic Test Client",
                "redirect_uris": ["https://client.example/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "notebooklm:access",
            },
        )
        assert registration.status_code == 201
        client_info = registration.json()

        verifier = "verifier-1234567890"
        authorize = client.get(
            "/authorize",
            params={
                "client_id": client_info["client_id"],
                "redirect_uri": "https://client.example/callback",
                "response_type": "code",
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "scope": "notebooklm:access",
                "state": "opaque-state",
                "resource": "http://localhost:8006/mcp",
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302
        consent_url = authorize.headers["location"]
        assert consent_url.startswith("http://localhost:8006/oauth/consent?grant_id=")

        consent = client.get(consent_url)
        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text

        bad_password = client.post(
            "/oauth/consent",
            data={
                "grant_id": parse_qs(urlparse(consent_url).query)["grant_id"][0],
                "password": "wrong",
            },
        )
        assert bad_password.status_code == 403

        approve = client.post(
            "/oauth/consent",
            data={
                "grant_id": parse_qs(urlparse(consent_url).query)["grant_id"][0],
                "password": "secret-pass",
                "action": "approve",
            },
            follow_redirects=False,
        )
        assert approve.status_code == 302
        callback_url = approve.headers["location"]
        callback_query = parse_qs(urlparse(callback_url).query)
        assert callback_query["state"] == ["opaque-state"]
        code = callback_query["code"][0]

        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://client.example/callback",
                "client_id": client_info["client_id"],
                "code_verifier": verifier,
                "resource": "http://localhost:8006/mcp",
            },
        )
        assert token.status_code == 200
        token_json = token.json()
        assert token_json["token_type"] == "Bearer"
        assert token_json["refresh_token"]

        unauthorized = client.post("/mcp", json={})
        assert unauthorized.status_code == 401
        assert "resource_metadata=" in unauthorized.headers["www-authenticate"]

        authorized = client.post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {token_json['access_token']}"},
        )
        assert authorized.status_code != 401
        assert authorized.status_code != 403


def test_remote_config_requires_https_outside_localhost(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "http://example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "must use https outside localhost" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-HTTPS public URL")
