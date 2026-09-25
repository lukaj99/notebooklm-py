"""The server as MCP clients see it, in both protocol eras.

Everything here goes through the SDK layer a real client meets, never the tool
functions directly, and nothing reaches Google: no test calls a tool handler or
reads a resource that would create a NotebookLM client.

- ``legacy``: the 2025-11-25 ``initialize`` handshake (today's connectors).
- ``auto``: negotiates 2026-07-28 (stateless requests, no handshake).

The raw-HTTP tests cover what the in-memory client cannot: routing headers,
``server/discover`` over the wire, and the GET behaviour of ``/mcp``.
"""

from __future__ import annotations

import json

import pytest
from mcp.client import Client
from mcp.types import TextContent
from starlette.testclient import TestClient

from notebooklm_mcp.server import create_mcp_server

pytestmark = pytest.mark.timeout(30)

ERAS = ["legacy", "auto"]
MODERN = "2026-07-28"
LEGACY = "2025-11-25"
TOOLS = {
    "list_notebooks",
    "create_notebook",
    "get_notebook",
    "list_sources",
    "add_source",
    "ask_question",
    "generate_artifact",
    "get_artifact_status",
}
LIST_TTL_MS = 300_000


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "version"), [("legacy", LEGACY), ("auto", MODERN)])
async def test_each_era_negotiates_its_protocol_version(mode, version):
    async with Client(create_mcp_server(), mode=mode) as client:
        assert client.protocol_version == version


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ERAS)
async def test_tools_list_with_object_schemas(mode):
    # v2 validates results against the protocol schema before sending: one
    # malformed tool would fail tools/list for every client.
    async with Client(create_mcp_server(), mode=mode) as client:
        tools = (await client.list_tools()).tools

    assert {t.name for t in tools} == TOOLS
    assert all(t.input_schema.get("type") == "object" for t in tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ERAS)
async def test_resources_and_templates_list(mode):
    async with Client(create_mcp_server(), mode=mode) as client:
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates

    assert [str(r.uri) for r in resources] == ["notebooklm://notebooks"]
    assert [t.uri_template for t in templates] == ["notebooklm://notebooks/{notebook_id}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ERAS)
async def test_invalid_arguments_are_a_tool_error_before_any_google_call(mode):
    # Argument validation runs before the handler, so no NotebookLM client is
    # created. A missing argument must be readable isError text for the model.
    async with Client(create_mcp_server(), mode=mode) as client:
        result = await client.call_tool("create_notebook", {})

    assert result.is_error
    text = "".join(c.text for c in result.content if isinstance(c, TextContent))
    assert "title" in text


@pytest.mark.asyncio
async def test_list_results_carry_private_cache_hints():
    async with Client(create_mcp_server(), mode="auto") as client:
        results = [
            await client.list_tools(),
            await client.list_resources(),
            await client.list_resource_templates(),
            await client.list_prompts(),
        ]

    for result in results:
        assert (result.ttl_ms, result.cache_scope) == (LIST_TTL_MS, "private"), result


# --- raw HTTP ----------------------------------------------------------------


def _modern(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "wire-test", "version": "0"},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**(params or {}), "_meta": meta},
    }


def _headers(method: str, name: str | None = None) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": MODERN,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    return headers


def _json_body(response) -> dict:
    """The JSON-RPC message, whether sent as JSON or as a single SSE event."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


@pytest.fixture()
def http_client():
    # OAuth disabled: these checks are about the MCP transport, not auth.
    with TestClient(
        create_mcp_server().streamable_http_app(), base_url="http://localhost:8006"
    ) as client:
        yield client


def test_server_discover_over_http(http_client):
    response = http_client.post(
        "/mcp", json=_modern("server/discover"), headers=_headers("server/discover")
    )

    assert response.status_code == 200, response.text
    result = _json_body(response)["result"]
    # Discover lists the stateless versions; 2025-11-25 clients are served
    # through initialize instead (see test_each_era_negotiates_its_protocol_version).
    assert MODERN in result["supportedVersions"]
    assert (result["ttlMs"], result["cacheScope"]) == (LIST_TTL_MS, "private")


def test_mcp_method_header_must_match_the_body(http_client):
    response = http_client.post(
        "/mcp", json=_modern("server/discover"), headers=_headers("tools/list")
    )

    assert response.status_code == 400
    assert _json_body(response)["error"]["code"] == -32020


def test_mcp_name_header_must_match_the_tool_name(http_client):
    body = _modern("tools/call", {"name": "list_notebooks", "arguments": {}})
    response = http_client.post("/mcp", json=body, headers=_headers("tools/call", "get_notebook"))

    assert response.status_code == 400
    assert _json_body(response)["error"]["code"] == -32020


def test_get_without_a_session_is_refused_not_held_open(http_client):
    # The server runs stateful Streamable HTTP; a GET stream needs a session.
    # Without one it must be refused at once, never left as an open stream.
    response = http_client.get("/mcp", headers={"accept": "text/event-stream"})

    assert 400 <= response.status_code < 500
    assert not response.headers.get("content-type", "").startswith("text/event-stream")
