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


@pytest.mark.asyncio
async def test_access_and_refresh_tokens_are_hashed_at_rest(tmp_path: Path) -> None:
    """The persisted state file must never contain a raw bearer token."""

    config = _config(tmp_path)
    provider = FileBackedOAuthProvider(config)
    client = OAuthClientInformationFull(
        client_id="client-456",
        client_name="Claude Code",
        redirect_uris=["https://claude.example.com/callback"],
        token_endpoint_auth_method="client_secret_basic",
    )
    await provider.register_client(client)

    redirect_to = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-456",
            scopes=["notebooklm:access"],
            code_challenge="challenge-456",
            redirect_uri="https://claude.example.com/callback",
            redirect_uri_provided_explicitly=True,
            resource="https://notebooklm.example.com/mcp",
        ),
    )
    grant_id = parse_qs(urlparse(redirect_to).query)["grant_id"][0]
    approved_redirect = await provider.approve_pending_authorization(grant_id)
    assert approved_redirect is not None
    code_value = parse_qs(urlparse(approved_redirect).query)["code"][0]

    authorization_code = await provider.load_authorization_code(client, code_value)
    assert authorization_code is not None
    token = await provider.exchange_authorization_code(client, authorization_code)

    raw_state = config.oauth_store_path.read_text()
    assert token.access_token not in raw_state
    assert token.refresh_token not in raw_state

    # Round trip still works: the raw token hashes to the same key on lookup.
    access = await provider.load_access_token(token.access_token)
    assert access is not None
    assert access.token != token.access_token


@pytest.mark.asyncio
async def test_auto_approve_does_not_bypass_consent_for_unknown_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A never-approved client must not be auto-approved, even with
    OAUTH_AUTO_APPROVE=true — otherwise the open dynamic client
    registration + /authorize endpoints would let anyone mint a token
    with zero credentials."""

    monkeypatch.setenv("OAUTH_AUTO_APPROVE", "true")
    provider = FileBackedOAuthProvider(_config(tmp_path))
    client = OAuthClientInformationFull(
        client_id="never-approved-client",
        client_name="Untrusted Client",
        redirect_uris=["https://attacker.example/callback"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)

    redirect_to = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-attack",
            scopes=["notebooklm:access"],
            code_challenge="challenge-attack",
            redirect_uri="https://attacker.example/callback",
            redirect_uri_provided_explicitly=True,
            resource="https://notebooklm.example.com/mcp",
        ),
    )

    # Must fall through to the consent page, not an approved redirect.
    assert redirect_to.startswith(f"{provider.config.issuer_url}/oauth/consent?grant_id=")
    query = parse_qs(urlparse(redirect_to).query)
    grant_id = query["grant_id"][0]
    pending = await provider.get_pending_authorization(grant_id)
    assert pending is not None


@pytest.mark.asyncio
async def test_auto_approve_bypasses_consent_for_trusted_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once a client has been through owner-gated consent once
    (trust_client), subsequent authorize() calls may auto-approve."""

    monkeypatch.setenv("OAUTH_AUTO_APPROVE", "true")
    provider = FileBackedOAuthProvider(_config(tmp_path))
    client = OAuthClientInformationFull(
        client_id="returning-client",
        client_name="Claude Desktop",
        redirect_uris=["https://claude.example/callback"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)
    await provider.trust_client("returning-client")

    redirect_to = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-returning",
            scopes=["notebooklm:access"],
            code_challenge="challenge-returning",
            redirect_uri="https://claude.example/callback",
            redirect_uri_provided_explicitly=True,
            resource="https://notebooklm.example.com/mcp",
        ),
    )

    # Must be an approved redirect straight back to the client (a "code="
    # query param on the client's own redirect_uri), not a consent page.
    assert "code=" in redirect_to
    assert "/oauth/consent" not in redirect_to
