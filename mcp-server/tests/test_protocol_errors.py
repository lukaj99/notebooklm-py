from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import INVALID_PARAMS

from notebooklm_mcp.server import RESOURCE_NOT_FOUND, create_mcp_server

pytestmark = pytest.mark.timeout(30)


@pytest.mark.asyncio
async def test_unknown_resource_returns_resource_not_found_code():
    """An unknown URI gets -32002 with the URI in data, not the SDK's code 0."""
    async with create_connected_server_and_client_session(create_mcp_server()) as client:
        with pytest.raises(McpError) as excinfo:
            await client.read_resource("notebooklm://no-such-resource")

    assert excinfo.value.error.code == RESOURCE_NOT_FOUND == -32002
    assert excinfo.value.error.data == {"uri": "notebooklm://no-such-resource"}


@pytest.mark.asyncio
async def test_registered_resources_still_read():
    """The not-found check only rejects unregistered URIs (fixed and templated)."""
    mcp = create_mcp_server()

    @mcp.resource("test://fixed")
    def fixed_resource() -> str:
        return "fixed-ok"

    @mcp.resource("test://items/{item_id}")
    def item_resource(item_id: str) -> str:
        return f"item-{item_id}"

    async with create_connected_server_and_client_session(mcp) as client:
        fixed = await client.read_resource("test://fixed")
        templated = await client.read_resource("test://items/42")

    assert fixed.contents[0].text == "fixed-ok"
    assert templated.contents[0].text == "item-42"


@pytest.mark.asyncio
async def test_unknown_prompt_returns_invalid_params():
    """An unknown prompt name is invalid params (-32602), not the SDK's code 0."""
    async with create_connected_server_and_client_session(create_mcp_server()) as client:
        with pytest.raises(McpError) as excinfo:
            await client.get_prompt("no-such-prompt")

    assert excinfo.value.error.code == INVALID_PARAMS


def test_local_cli_no_longer_offers_sse(monkeypatch):
    """The deprecated HTTP+SSE transport is gone; argparse rejects it loudly."""
    from notebooklm_mcp import server

    monkeypatch.setattr("sys.argv", ["notebooklm-mcp-local", "sse"])
    with pytest.raises(SystemExit) as excinfo:
        server.main()

    assert excinfo.value.code == 2
