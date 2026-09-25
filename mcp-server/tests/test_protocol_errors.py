from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from notebooklm_mcp.server import RESOURCE_NOT_FOUND, create_mcp_server

pytestmark = pytest.mark.timeout(30)

# legacy: the 2025-11-25 initialize handshake. auto: negotiates 2026-07-28.
ERAS = {"legacy": RESOURCE_NOT_FOUND, "auto": INVALID_PARAMS}


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "expected_code"), ERAS.items())
async def test_unknown_resource_code_follows_the_protocol_era(mode, expected_code):
    """-32002 for 2025-11-25 clients, -32602 in 2026-07-28, URI in data either way."""
    async with Client(create_mcp_server(), mode=mode) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.read_resource("notebooklm://no-such-resource")

    assert excinfo.value.error.code == expected_code
    assert excinfo.value.error.data == {"uri": "notebooklm://no-such-resource"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ERAS)
async def test_registered_resources_still_read(mode):
    """The not-found check only rejects unregistered URIs (fixed and templated)."""
    mcp = create_mcp_server()

    @mcp.resource("test://fixed")
    def fixed_resource() -> str:
        return "fixed-ok"

    @mcp.resource("test://items/{item_id}")
    def item_resource(item_id: str) -> str:
        return f"item-{item_id}"

    async with Client(mcp, mode=mode) as client:
        fixed = await client.read_resource("test://fixed")
        templated = await client.read_resource("test://items/42")

    assert fixed.contents[0].text == "fixed-ok"
    assert templated.contents[0].text == "item-42"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ERAS)
async def test_unknown_prompt_returns_invalid_params(mode):
    """An unknown prompt name is invalid params (-32602) in both eras."""
    async with Client(create_mcp_server(), mode=mode) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.get_prompt("no-such-prompt")

    assert excinfo.value.error.code == INVALID_PARAMS


def test_local_cli_no_longer_offers_sse(monkeypatch):
    """The deprecated HTTP+SSE transport is gone; argparse rejects it loudly."""
    from notebooklm_mcp import server

    monkeypatch.setattr("sys.argv", ["notebooklm-mcp-local", "sse"])
    with pytest.raises(SystemExit) as excinfo:
        server.main()

    assert excinfo.value.code == 2
