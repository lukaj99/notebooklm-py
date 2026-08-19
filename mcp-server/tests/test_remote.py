from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from notebooklm_mcp.config import RemoteServerConfig, redirect_uri_matches
from notebooklm_mcp.oauth import FileBackedOAuthProvider
from notebooklm_mcp.remote import build_asgi_app, build_auth_settings
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

    # base_url must match an allowed_hosts entry (server.py pins
    # allowed_hosts to loopback + the production domain): TestClient's
    # default base_url of http://testserver sends Host: testserver, which
    # transport_security._validate_host rejects with 421 before ever
    # reaching the /mcp handler — masking real assertions about /mcp
    # behavior behind a 421 that happens to also not be 401/403.
    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
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

        # A genuine JSON-RPC "initialize" call (matching what a real MCP
        # client sends, including the Accept header streamable-http
        # requires) confirms the request actually reached protocol
        # handling, rather than merely checking "not 401/403" — which a
        # 421 Invalid Host (wrong TestClient base_url) or 403 Invalid
        # Origin (unset allowed_origins) would also satisfy without the
        # request ever being processed.
        initialize_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
        authorized = client.post(
            "/mcp",
            json=initialize_body,
            headers={
                "Authorization": f"Bearer {token_json['access_token']}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert authorized.status_code == 200, authorized.text

        # Browser-based MCP clients (the claude.ai connector UI) always send
        # an Origin header on cross-origin POSTs — the plain server-to-server
        # call above never exercises this path.
        # TransportSecuritySettings.allowed_origins must include the caller's
        # origin or mcp.server.transport_security._validate_origin rejects
        # it with 403 "Invalid Origin header" regardless of a valid token.
        authorized_from_browser = client.post(
            "/mcp",
            json=initialize_body,
            headers={
                "Authorization": f"Bearer {token_json['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Origin": "https://claude.ai",
            },
        )
        assert authorized_from_browser.status_code == 200, authorized_from_browser.text


def test_mcp_route_answers_cors_preflight(monkeypatch, tmp_path):
    """Browser MCP clients (e.g. the claude.ai connector) send an OPTIONS
    preflight to /mcp before their authenticated POST. Without app-level
    CORS, that preflight hits RequireAuthMiddleware first (no Authorization
    header on a preflight) and gets a bare 401 with no
    Access-Control-Allow-* headers — the browser then blocks the real
    request as a CORS failure even though a valid token would have worked
    fine server-to-server. See build_asgi_app()."""

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

    with TestClient(build_asgi_app(mcp)) as client:
        preflight = client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"
        assert "POST" in preflight.headers["access-control-allow-methods"]


def test_bare_protected_resource_metadata_available(monkeypatch, tmp_path):
    """RFC 9728 §3.1 only requires the resource-path-suffixed location
    (/.well-known/oauth-protected-resource/mcp), which the mcp SDK already
    serves — a spec-compliant client is meant to reach it via the
    `resource_metadata` hint in the 401 WWW-Authenticate header on /mcp.
    In practice the claude.ai connector requests the bare root path
    directly instead of following that hint (confirmed from live connector
    logs: every attempt hits the bare path and 404s without ever touching
    /mcp first). Since this server has exactly one resource, serving
    identical metadata at both locations is spec-safe and fixes discovery
    for that client. See BareProtectedResourceMetadataMiddleware."""

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

    with TestClient(build_asgi_app(mcp, config)) as client:
        bare = client.get("/.well-known/oauth-protected-resource")
        assert bare.status_code == 200
        assert bare.json()["resource"] == "http://localhost:8006/mcp"
        assert bare.json()["authorization_servers"] == ["http://localhost:8006/"]

        # The resource-path-suffixed location must keep working too — this
        # is additive, not a replacement.
        suffixed = client.get("/.well-known/oauth-protected-resource/mcp")
        assert suffixed.status_code == 200
        assert suffixed.json() == bare.json()


def test_remote_config_requires_https_outside_localhost(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "http://example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "must use https outside localhost" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-HTTPS public URL")


def test_trusted_access_emails_requires_loopback_host(monkeypatch):
    """trusted_access_emails trusts a header (cf-access-authenticated-user-
    email) that is only safe if this process is unreachable except through a
    fronting proxy that strips any client-supplied copy and sets it from an
    authenticated session. Binding anywhere else would let a direct request
    forge that header."""

    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "loopback address" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-loopback host with trusted emails")


def test_trusted_access_emails_allows_loopback_host(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")

    config = RemoteServerConfig.from_env()
    assert config.trusted_access_emails == ("owner@example.com",)


def test_auto_approve_redirect_uris_parsed(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")
    monkeypatch.setenv(
        "NOTEBOOKLM_MCP_AUTO_APPROVE_REDIRECT_URIS",
        "https://claude.ai/api/mcp/auth_callback, http://localhost:*/callback",
    )

    config = RemoteServerConfig.from_env()

    assert config.auto_approve_redirect_uris == (
        "https://claude.ai/api/mcp/auth_callback",
        "http://localhost:*/callback",
    )


def test_auto_approve_redirect_uris_defaults_to_empty(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")

    assert RemoteServerConfig.from_env().auto_approve_redirect_uris == ()


def test_auto_approve_redirect_uris_requires_trusted_emails(monkeypatch):
    """Silent approval is granted on behalf of a proven owner identity. With
    no trusted-email header to prove it, there is no identity to act for."""

    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")
    monkeypatch.setenv(
        "NOTEBOOKLM_MCP_AUTO_APPROVE_REDIRECT_URIS",
        "https://claude.ai/api/mcp/auth_callback",
    )

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "TRUSTED_ACCESS_EMAILS" in str(exc)
    else:
        raise AssertionError("Expected ValueError without trusted access emails")


def test_auto_approve_redirect_uris_rejects_non_http(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")
    monkeypatch.setenv("NOTEBOOKLM_MCP_AUTO_APPROVE_REDIRECT_URIS", "ftp://example.com/cb")

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "http or https" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-HTTP redirect URI")


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("https://claude.ai/api/mcp/auth_callback", True),
        ("http://localhost:51483/callback", True),
        ("http://127.0.0.1:8080/callback", True),
        # Not on the list at all.
        ("https://attacker.example.com/callback", False),
        # Right shape, wrong host — the wildcard is loopback-only.
        ("http://attacker.example.com:8080/callback", False),
        # Wildcard stands in for a port, not for an arbitrary path segment.
        ("http://localhost:8080/evil", False),
        # A non-numeric "port" must not satisfy the wildcard.
        ("http://localhost:evil/callback", False),
        # Prefix/suffix present but the callback is nested deeper.
        ("https://claude.ai/api/mcp/auth_callback/extra", False),
    ],
)
def test_redirect_uri_matches(candidate, expected):
    patterns = (
        "https://claude.ai/api/mcp/auth_callback",
        "http://localhost:*/callback",
        "http://127.0.0.1:*/callback",
    )

    assert redirect_uri_matches(candidate, patterns) is expected


def test_redirect_uri_matches_empty_allowlist_matches_nothing():
    assert redirect_uri_matches("https://claude.ai/api/mcp/auth_callback", ()) is False


def _proxied(headers: dict[str, str]) -> dict[str, str]:
    """Headers as the fronting proxy would present them."""

    return {"x-auth-gate-secret": "gate-secret", **headers}


def _silent_approval_client(monkeypatch, tmp_path, *, allowlist: str):
    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "http://localhost:8006")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "secret-pass")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")
    monkeypatch.setenv("NOTEBOOKLM_MCP_AUTO_APPROVE_REDIRECT_URIS", allowlist)
    monkeypatch.setenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", "gate-secret")
    monkeypatch.setenv("NOTEBOOKLM_MCP_OAUTH_STORE_PATH", str(tmp_path / "oauth-state.json"))

    config = RemoteServerConfig.from_env()
    provider = FileBackedOAuthProvider(config)
    mcp = create_mcp_server(
        host=config.host,
        port=config.port,
        auth_settings=build_auth_settings(config),
        auth_provider=provider,
        oauth_password=config.oauth_password,
        trusted_access_emails=config.trusted_access_emails,
        auto_approve_redirect_uris=config.auto_approve_redirect_uris,
        proxy_shared_secret=config.proxy_shared_secret,
    )
    return mcp


def _consent_url_for(client, redirect_uri: str) -> str:
    registration = client.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "notebooklm:access",
        },
    )
    assert registration.status_code == 201

    authorize = client.get(
        "/authorize",
        params={
            "client_id": registration.json()["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": _pkce_challenge("verifier-1234567890"),
            "code_challenge_method": "S256",
            "scope": "notebooklm:access",
            "state": "opaque-state",
            "resource": "http://localhost:8006/mcp",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    return authorize.headers["location"]


def test_consent_get_silently_approves_allowlisted_redirect_uri(monkeypatch, tmp_path):
    """A brand-new client_id (claude.ai registers one per connect) still gets
    through without a click, provided the proxy proved the owner identity and
    the callback is one we published."""

    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")

        consent = client.get(
            consent_url,
            headers=_proxied({"cf-access-authenticated-user-email": "owner@example.com"}),
            follow_redirects=False,
        )

        assert consent.status_code == 302
        callback = urlparse(consent.headers["location"])
        assert f"{callback.scheme}://{callback.netloc}{callback.path}" == (
            "https://claude.ai/api/mcp/auth_callback"
        )
        query = parse_qs(callback.query)
        assert query["state"] == ["opaque-state"]
        assert query["code"]


def test_consent_get_requires_click_for_unlisted_redirect_uri(monkeypatch, tmp_path):
    """The allowlist is what makes GET-triggered approval safe against a
    cross-site navigation: a callback we never published still lands on the
    interactive page, where the client name is shown before anything is
    granted."""

    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://attacker.example/callback")

        consent = client.get(
            consent_url,
            headers=_proxied({"cf-access-authenticated-user-email": "owner@example.com"}),
            follow_redirects=False,
        )

        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text


def test_consent_get_requires_click_without_trusted_identity(monkeypatch, tmp_path):
    """No proven owner identity means no silent approval, even for a callback
    that is on the allowlist."""

    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")

        consent = client.get(consent_url, follow_redirects=False)

        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text


def test_consent_get_rejects_untrusted_identity(monkeypatch, tmp_path):
    """An address the proxy authenticated but that is not an owner (the SSO
    allowlist is broader than NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS) must not
    silently approve."""

    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")

        consent = client.get(
            consent_url,
            headers=_proxied({"cf-access-authenticated-user-email": "someone-else@example.com"}),
            follow_redirects=False,
        )

        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text


def test_trusted_access_emails_requires_proxy_shared_secret(monkeypatch):
    """Nothing in-process can verify that the proxy stripped a client-supplied
    identity header, so a proxy misconfiguration would fail open silently.
    Refusing to start without the shared secret makes that failure loud."""

    monkeypatch.setenv("NOTEBOOKLM_MCP_PUBLIC_URL", "https://notebooklm.example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS", "owner@example.com")
    monkeypatch.setenv("NOTEBOOKLM_MCP_HOST", "127.0.0.1")
    monkeypatch.delenv("NOTEBOOKLM_MCP_PROXY_SHARED_SECRET", raising=False)

    try:
        RemoteServerConfig.from_env()
    except ValueError as exc:
        assert "NOTEBOOKLM_MCP_PROXY_SHARED_SECRET" in str(exc)
    else:
        raise AssertionError("Expected ValueError without a proxy shared secret")


def test_consent_ignores_identity_without_gate_secret(monkeypatch, tmp_path):
    """A forged identity header that did not come through the proxy proves
    nothing, so it must not silently approve."""

    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")

        consent = client.get(
            consent_url,
            headers={"cf-access-authenticated-user-email": "owner@example.com"},
            follow_redirects=False,
        )

        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text


def test_consent_ignores_identity_with_wrong_gate_secret(monkeypatch, tmp_path):
    mcp = _silent_approval_client(
        monkeypatch, tmp_path, allowlist="https://claude.ai/api/mcp/auth_callback"
    )

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")

        consent = client.get(
            consent_url,
            headers={
                "x-auth-gate-secret": "not-the-secret",
                "cf-access-authenticated-user-email": "owner@example.com",
            },
            follow_redirects=False,
        )

        assert consent.status_code == 200
        assert "Authorize NotebookLM MCP" in consent.text


def test_consent_post_rejects_forged_identity_without_gate_secret(monkeypatch, tmp_path):
    """The POST path is the one that actually grants. With trusted emails
    configured there is no password to fall back on, so a forged identity
    header must be refused outright rather than approved."""

    mcp = _silent_approval_client(monkeypatch, tmp_path, allowlist="")

    with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8006") as client:
        consent_url = _consent_url_for(client, "https://claude.ai/api/mcp/auth_callback")
        grant_id = parse_qs(urlparse(consent_url).query)["grant_id"][0]

        forged = client.post(
            "/oauth/consent",
            data={"grant_id": grant_id, "action": "approve"},
            headers={"cf-access-authenticated-user-email": "owner@example.com"},
            follow_redirects=False,
        )

        assert forged.status_code == 403
