from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from notebooklm_mcp.config import RemoteServerConfig
from notebooklm_mcp.oauth import FileBackedOAuthProvider


def _config(tmp_path: Path) -> RemoteServerConfig:
    return RemoteServerConfig(
        host="127.0.0.1",
        port=8006,
        public_base_url="https://notebooklm.example.com",
        oauth_password="secret-password",
        oauth_store_path=tmp_path / "oauth-state.json",
        required_scopes=("notebooklm:access",),
        service_documentation_url="https://docs.example.com/notebooklm-mcp",
        access_token_ttl_seconds=3600,
        refresh_token_ttl_seconds=86400,
        authorization_code_ttl_seconds=600,
        client_secret_expiry_seconds=None,
        tls_certfile=None,
        tls_keyfile=None,
    )


@pytest.mark.asyncio
async def test_file_backed_oauth_provider_round_trip(tmp_path: Path) -> None:
    provider = FileBackedOAuthProvider(_config(tmp_path))
    client = OAuthClientInformationFull(
        client_id="client-123",
        client_name="Claude Code",
        redirect_uris=["https://claude.example.com/callback"],
        token_endpoint_auth_method="client_secret_basic",
    )

    await provider.register_client(client)

    redirect_to = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-123",
            scopes=["notebooklm:access"],
            code_challenge="challenge-123",
            redirect_uri="https://claude.example.com/callback",
            redirect_uri_provided_explicitly=True,
            resource="https://notebooklm.example.com/mcp",
        ),
    )

    consent_query = parse_qs(urlparse(redirect_to).query)
    grant_id = consent_query["grant_id"][0]

    pending = await provider.get_pending_authorization(grant_id)
    assert pending is not None
    assert pending.client_id == "client-123"

    approved_redirect = await provider.approve_pending_authorization(grant_id)
    assert approved_redirect is not None

    approved_query = parse_qs(urlparse(approved_redirect).query)
    code_value = approved_query["code"][0]
    assert approved_query["state"][0] == "state-123"

    authorization_code = await provider.load_authorization_code(client, code_value)
    assert authorization_code is not None

    token = await provider.exchange_authorization_code(client, authorization_code)
    assert token.access_token
    assert token.refresh_token
    assert token.scope == "notebooklm:access"

    access = await provider.load_access_token(token.access_token)
    refresh = await provider.load_refresh_token(client, token.refresh_token)

    assert access is not None
    assert access.resource == "https://notebooklm.example.com/mcp"
    assert access.scopes == ["notebooklm:access"]
    assert refresh is not None
    assert refresh.resource == "https://notebooklm.example.com/mcp"
