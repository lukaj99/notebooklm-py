from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from notebooklm_mcp.config import RemoteServerConfig
from notebooklm_mcp.oauth import FileBackedOAuthProvider
from notebooklm_mcp.remote import build_auth_settings
from notebooklm_mcp.server import _add_source_for_type, create_mcp_server


class RecordingSources:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def add_url(self, notebook_id: str, content: str):
        self.calls.append(("add_url", (notebook_id, content)))
        return SimpleNamespace(
            id="src",
            title="title",
            url=content,
            kind=SimpleNamespace(value="web_page"),
            status=2,
            created_at=None,
        )

    async def add_text(self, notebook_id: str, title: str, content: str):
        self.calls.append(("add_text", (notebook_id, title, content)))
        return SimpleNamespace(
            id="src",
            title=title,
            url=None,
            kind=SimpleNamespace(value="text"),
            status=2,
            created_at=None,
        )

    async def add_file(self, notebook_id: str, path: Path):
        self.calls.append(("add_file", (notebook_id, path)))
        return SimpleNamespace(
            id="src",
            title=path.name,
            url=None,
            kind=SimpleNamespace(value="file"),
            status=2,
            created_at=None,
        )


class RecordingClient:
    def __init__(self) -> None:
        self.sources = RecordingSources()


def _remote_config(tmp_path: Path) -> RemoteServerConfig:
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
async def test_add_source_dispatches_to_correct_client_methods(tmp_path: Path) -> None:
    client = RecordingClient()

    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="url",
        content="https://example.com",
        title=None,
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="text",
        content="body text",
        title="Notes",
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="youtube",
        content="https://youtube.com/watch?v=abc123",
        title=None,
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="file",
        content=str(tmp_path / "sample.txt"),
        title=None,
    )

    assert client.sources.calls == [
        ("add_url", ("nb-1", "https://example.com")),
        ("add_text", ("nb-1", "Notes", "body text")),
        ("add_url", ("nb-1", "https://youtube.com/watch?v=abc123")),
        ("add_file", ("nb-1", Path(tmp_path / "sample.txt").expanduser())),
    ]


def test_remote_server_exposes_http_and_oauth_metadata(tmp_path: Path) -> None:
    config = _remote_config(tmp_path)
    provider = FileBackedOAuthProvider(config)
    app = create_mcp_server(
        host=config.host,
        port=config.port,
        auth_settings=build_auth_settings(config),
        auth_provider=provider,
        oauth_password=config.oauth_password,
    ).streamable_http_app()

    client = TestClient(app)

    root = client.get("/")
    health = client.get("/health")
    healthz = client.get("/healthz")
    auth_metadata = client.get("/.well-known/oauth-authorization-server")
    resource_metadata = client.get("/.well-known/oauth-protected-resource/mcp")
    unauthorized = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )

    assert root.status_code == 200
    assert root.json()["transport"] == "streamable-http"
    assert root.json()["oauth_enabled"] is True
    assert health.status_code == 200
    assert healthz.status_code == 200
    assert auth_metadata.status_code == 200
    assert auth_metadata.json()["issuer"] == "https://notebooklm.example.com/"
    assert resource_metadata.status_code == 200
    assert resource_metadata.json()["resource"] == "https://notebooklm.example.com/mcp"
    assert unauthorized.status_code == 401
    assert "resource_metadata=" in unauthorized.headers["www-authenticate"]
