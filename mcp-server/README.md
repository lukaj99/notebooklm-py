# NotebookLM MCP Server

Remote and local MCP server for Google NotebookLM, built on FastMCP.

## What it supports

- `stdio` for local Claude / MCP clients
- Streamable HTTP for remote Anthropic-compatible connectors
- OAuth 2.1 resource server: bearer tokens issued by an external OIDC provider
  (Pocket ID), validated here; this server issues no tokens
- NotebookLM tools for notebooks, sources, chat, and artifact generation

The server itself uses your existing `notebooklm login` session on the host machine.

## Installation

```bash
cd mcp-server
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

Authenticate NotebookLM first from the main package:

```bash
notebooklm login
```

## Local Usage

Run the MCP server over stdio:

```bash
notebooklm-mcp-local
```

Claude / MCP client config:

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/notebooklm-py/mcp-server",
        "run",
        "notebooklm-mcp-local"
      ]
    }
  }
}
```

## Remote Usage

The remote entrypoint is Streamable HTTP behind bearer-token auth:

```bash
export MCP_RESOURCE_URL="https://notebooklm.example.com/mcp"
export OAUTH_ISSUER="https://auth.example.com"
export MCP_ALLOWED_SUBS="<pocket-id-user-id>[,<another>]"

notebooklm-mcp-remote
```

This serves the MCP endpoint at `MCP_RESOURCE_URL`.

If you want the process itself to terminate TLS instead of using a reverse proxy:

```bash
export NOTEBOOKLM_MCP_TLS_CERTFILE=/path/to/fullchain.pem
export NOTEBOOKLM_MCP_TLS_KEYFILE=/path/to/privkey.pem
notebooklm-mcp-remote
```

### Environment variables

- `MCP_RESOURCE_URL` (required)
  - Canonical public URL of the MCP endpoint. Every token's `aud` must contain it.
  - Must be HTTPS outside localhost
- `OAUTH_ISSUER`
  - The authorization server. Defaults to `https://auth.jovanovic.org.uk`
- `MCP_ALLOWED_SUBS`
  - Comma-separated `sub` claims allowed to use this server's NotebookLM login.
    Unset or empty rejects every request
- `NOTEBOOKLM_MCP_HOST`, `NOTEBOOKLM_MCP_PORT`
- `NOTEBOOKLM_MCP_TLS_CERTFILE`, `NOTEBOOKLM_MCP_TLS_KEYFILE`

### Token requirements

`Authorization: Bearer <JWT>` on every `/mcp` request. The JWT must be RS256-signed
by the issuer's JWKS (`<issuer>/.well-known/jwks.json`), carry `iss`, `aud`, `sub`
and `exp`, have `aud` containing `MCP_RESOURCE_URL`, have scope `mcp:use` (`scp`
array or space-separated `scope`), and have a `sub` in `MCP_ALLOWED_SUBS`.
Anything else gets `401` with a `WWW-Authenticate: Bearer resource_metadata=...`
challenge. No request header other than `Authorization` grants access.

## Anthropic / Claude Setup

Use the remote MCP URL with HTTP transport:

```json
{
  "mcpServers": {
    "notebooklm-remote": {
      "transport": "http",
      "url": "https://notebooklm.example.com/mcp"
    }
  }
}
```

Or with Claude Code:

```bash
claude mcp add --transport http notebooklm https://notebooklm.example.com/mcp
```

On first connect, the client hits `/mcp`, receives the `401` challenge, reads the
protected-resource metadata, and runs the authorization flow against the issuer
directly.

## Endpoints

- MCP: `/mcp`
- Health: `/health`
- Health alias: `/healthz`
- Protected resource metadata: `/.well-known/oauth-protected-resource` and
  `/.well-known/oauth-protected-resource/mcp`

There is no `/authorize`, `/token`, `/register`, `/revoke` or authorization-server
metadata here.

## Available tools

- `list_notebooks`
- `create_notebook`
- `get_notebook`
- `list_sources`
- `add_source`
- `ask_question`
- `generate_artifact`
- `get_artifact_status`

## Resources

- `notebooklm://notebooks`
- `notebooklm://notebooks/{notebook_id}`

## Protocol versions

The server runs on the `mcp` 2.x SDK (`MCPServer`) and serves both protocol eras
over stdio and Streamable HTTP. HTTP+SSE has been removed; `notebooklm-mcp-sse`
remains as an alias that starts the Streamable HTTP server.

- **2025-11-25**: the `initialize` handshake with an `Mcp-Session-Id`, as used by
  today's connectors. An unknown resource returns -32002 with the URI in `data`.
- **2026-07-28**: stateless requests with no handshake, `server/discover`, and
  `Mcp-Method`/`Mcp-Name` headers that must match the body (-32020 otherwise).
  An unknown resource returns -32602 with the URI in `data`.
- Either era: an unknown prompt returns -32602. A `GET /mcp` without a session is
  refused with a 4xx; it never opens a stream.
- Cache hints: `server/discover` and the tool, prompt, resource and template lists
  carry `ttlMs` 300000 with `cacheScope` `private` (every remote request is
  authenticated). `resources/read` carries none, because both resources are live
  NotebookLM data.
- No Tasks extension: `generate_artifact` already returns a task id at once and
  `get_artifact_status` polls it, so no tool blocks for long.

This package depends on plain `notebooklm-py`, not its `[mcp]` extra. That extra
installs standalone `fastmcp` 3.x for the root package's own MCP server, and
fastmcp 3.x requires `mcp<2`. As a result the root `notebooklm-mcp` console
script is installed in this venv but cannot start; run it from a venv with
`notebooklm-py[mcp]`.

## Development

```bash
ruff format src tests
ruff check src tests
pytest
```
