"""Pocket ID is the only authorization server; this process is a pure resource server.

Tokens are signed with a throwaway RSA key and the JWKS lookup is replaced, so
nothing here touches the network or Google.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from notebooklm_mcp.auth import REQUIRED_SCOPE, PocketIdTokenVerifier, build_jwks_client
from notebooklm_mcp.config import RemoteServerConfig
from notebooklm_mcp.remote import build_remote_app

ISSUER = "https://auth.jovanovic.org.uk"
RESOURCE = "https://notebook.jovanovic.org.uk/mcp"
PRM_URL = "https://notebook.jovanovic.org.uk/.well-known/oauth-protected-resource/mcp"
OWNER = "b9594f3a-2cd0-4e99-9560-cb49f59e77aa"
OTHER_USER = "11111111-2222-3333-4444-555555555555"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class FakeJwks:
    """Stands in for PyJWKClient: hands back the public half of the test key."""

    def get_signing_key_from_jwt(self, _token: str):
        return SimpleNamespace(key=_KEY.public_key())


def _token(*, key=_KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": OWNER,
        "exp": now + 600,
        "iat": now,
        "scp": [REQUIRED_SCOPE],
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def _config(allowed_subs=(OWNER,)) -> RemoteServerConfig:
    return RemoteServerConfig(
        host="127.0.0.1",
        port=8006,
        resource_url=RESOURCE,
        issuer=ISSUER,
        allowed_subs=tuple(allowed_subs),
        tls_certfile=None,
        tls_keyfile=None,
    )


def _client(allowed_subs=(OWNER,)) -> TestClient:
    config = _config(allowed_subs)
    verifier = PocketIdTokenVerifier(
        issuer=config.issuer,
        resource_url=config.resource_url,
        allowed_subs=config.allowed_subs,
        jwks_client=FakeJwks(),
    )
    # base_url must match transport security's allowed hosts (loopback + prod).
    return TestClient(
        build_remote_app(config, token_verifier=verifier), base_url="http://localhost:8006"
    )


def _post(client: TestClient, token: str | None = None, headers: dict | None = None):
    sent = dict(MCP_HEADERS)
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    sent.update(headers or {})
    return client.post("/mcp", json=INITIALIZE, headers=sent)


def _assert_rejected(response, *, invalid_token: bool) -> None:
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{PRM_URL}"' in challenge
    if invalid_token:
        assert 'error="invalid_token"' in challenge


def test_valid_owner_token_is_accepted():
    with _client() as client:
        response = _post(client, _token())

    assert response.status_code == 200
    # Streamable HTTP answers as a one-event SSE stream.
    assert '"name":"notebooklm"' in response.text


def test_audience_list_containing_the_resource_is_accepted():
    with _client() as client:
        response = _post(client, _token(aud=["https://other.example/mcp", RESOURCE]))

    assert response.status_code == 200


def test_space_separated_scope_claim_is_accepted():
    with _client() as client:
        response = _post(client, _token(scp=None, scope=f"openid {REQUIRED_SCOPE}"))

    assert response.status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://other.example/mcp"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 60},
        {"scp": ["openid"]},
        {"scp": None},
        {"sub": OTHER_USER},
        {"sub": None},
        {"exp": None},
        {"aud": None},
        {"iss": None},
    ],
    ids=[
        "wrong-aud",
        "wrong-iss",
        "expired",
        "missing-scope",
        "no-scope-claim",
        "sub-not-allowlisted",
        "no-sub",
        "no-exp",
        "no-aud",
        "no-iss",
    ],
)
def test_bad_token_is_rejected(overrides):
    with _client() as client:
        response = _post(client, _token(**overrides))

    _assert_rejected(response, invalid_token=True)


def test_token_signed_by_another_key_is_rejected():
    with _client() as client:
        response = _post(client, _token(key=_OTHER_KEY))

    _assert_rejected(response, invalid_token=True)


def test_hs256_token_is_rejected():
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": RESOURCE,
            "sub": OWNER,
            "exp": int(time.time()) + 60,
            "scp": [REQUIRED_SCOPE],
        },
        "x" * 64,
        algorithm="HS256",
    )
    with _client() as client:
        response = _post(client, forged)

    _assert_rejected(response, invalid_token=True)


@pytest.mark.parametrize("allowed", [(), ("",)])
def test_unset_allowlist_rejects_everyone(allowed):
    with _client(allowed_subs=allowed) as client:
        response = _post(client, _token())

    _assert_rejected(response, invalid_token=True)


def test_missing_token_is_401_with_resource_metadata_hint():
    with _client() as client:
        response = _post(client)

    _assert_rejected(response, invalid_token=False)


def test_spoofed_identity_headers_alone_do_not_grant_access():
    # The old gate trusted this pair, set by the fronting proxy. Nothing reads
    # them now, so a client sending them gets exactly what an anonymous one does.
    spoof = {
        "Cf-Access-Authenticated-User-Email": "owner@example.com",
        "X-Auth-Request-Email": "owner@example.com",
        "X-Auth-Gate-Secret": "anything",
    }
    with _client() as client:
        response = _post(client, headers=spoof)

    _assert_rejected(response, invalid_token=False)


def test_spoofed_identity_headers_do_not_rescue_a_bad_token():
    spoof = {"Cf-Access-Authenticated-User-Email": "owner@example.com"}
    with _client() as client:
        response = _post(client, _token(sub=OTHER_USER), headers=spoof)

    _assert_rejected(response, invalid_token=True)


def test_token_value_is_never_logged(caplog):
    token = _token(sub=OTHER_USER)
    with caplog.at_level("DEBUG"), _client() as client:
        _post(client, token)

    assert token not in caplog.text
    assert token.split(".")[1] not in caplog.text


EXPECTED_PRM = {
    "resource": RESOURCE,
    "authorization_servers": [ISSUER],
    "scopes_supported": [REQUIRED_SCOPE],
    "bearer_methods_supported": ["header"],
}


@pytest.mark.parametrize(
    "path",
    ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"],
)
def test_protected_resource_metadata_on_both_paths(path):
    with _client() as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.json() == EXPECTED_PRM


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/.well-known/oauth-authorization-server"),
        ("get", "/.well-known/openid-configuration"),
        ("post", "/register"),
        ("get", "/authorize"),
        ("post", "/token"),
        ("post", "/revoke"),
        ("get", "/oauth/consent"),
        ("post", "/oauth/consent"),
    ],
)
def test_builtin_authorization_server_is_gone(method, path):
    with _client() as client:
        response = getattr(client, method)(path)

    assert response.status_code in (404, 405)


def test_health_and_root_stay_public_and_advertise_no_oauth_server():
    with _client() as client:
        assert client.get("/health").status_code == 200
        root = client.get("/")

    body = root.json()
    assert root.status_code == 200
    assert "authorize" not in body["endpoints"]
    assert "oauth_metadata" not in body["endpoints"]


def test_cors_preflight_still_answered_without_a_token():
    with _client() as client:
        preflight = client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert preflight.status_code == 200
    assert "POST" in preflight.headers["access-control-allow-methods"]


def test_jwks_client_sends_a_non_default_user_agent():
    # Cloudflare's WAF 403s python-urllib's default UA, which would make every
    # token fail closed.
    client = build_jwks_client(ISSUER)

    assert client.uri == f"{ISSUER}/.well-known/jwks.json"
    user_agent = client.headers["User-Agent"]
    assert user_agent and "urllib" not in user_agent.lower()


class TestConfigFromEnv:
    def _env(self, monkeypatch, **values):
        for name in ("MCP_RESOURCE_URL", "OAUTH_ISSUER", "MCP_ALLOWED_SUBS"):
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    def test_defaults_and_allowlist_parsing(self, monkeypatch):
        self._env(
            monkeypatch, MCP_RESOURCE_URL=RESOURCE, MCP_ALLOWED_SUBS=f" {OWNER} , ,{OTHER_USER}"
        )

        config = RemoteServerConfig.from_env()

        assert config.resource_url == RESOURCE
        assert config.issuer == ISSUER
        assert config.allowed_subs == (OWNER, OTHER_USER)

    def test_unset_allowlist_parses_empty_not_error(self, monkeypatch):
        self._env(monkeypatch, MCP_RESOURCE_URL=RESOURCE)

        assert RemoteServerConfig.from_env().allowed_subs == ()

    def test_issuer_trailing_slash_is_normalised(self, monkeypatch):
        self._env(monkeypatch, MCP_RESOURCE_URL=RESOURCE, OAUTH_ISSUER=ISSUER + "/")

        assert RemoteServerConfig.from_env().issuer == ISSUER

    def test_resource_url_is_required(self, monkeypatch):
        self._env(monkeypatch)

        with pytest.raises(ValueError, match="MCP_RESOURCE_URL"):
            RemoteServerConfig.from_env()

    def test_resource_url_must_be_https_off_loopback(self, monkeypatch):
        self._env(monkeypatch, MCP_RESOURCE_URL="http://notebook.jovanovic.org.uk/mcp")

        with pytest.raises(ValueError, match="https"):
            RemoteServerConfig.from_env()

    def test_loopback_http_resource_is_allowed(self, monkeypatch):
        self._env(monkeypatch, MCP_RESOURCE_URL="http://localhost:8006/mcp")

        assert RemoteServerConfig.from_env().resource_url == "http://localhost:8006/mcp"
