# NotebookLM MCP Server

Remote and local MCP server for Google NotebookLM, built on FastMCP.

## What it supports

- `stdio` for local Claude / MCP clients
- Streamable HTTP for remote Anthropic-compatible connectors
- OAuth 2.1 authorization-code flow with PKCE
- Dynamic client registration, refresh tokens, and revocation
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
notebooklm-mcp
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
        "notebooklm-mcp"
      ]
    }
  }
}
```

## Remote Usage

The remote entrypoint is Streamable HTTP plus OAuth 2.1:

```bash
export NOTEBOOKLM_MCP_PUBLIC_URL="https://notebooklm.example.com"
export NOTEBOOKLM_MCP_OAUTH_PASSWORD="choose-a-strong-password"
export NOTEBOOKLM_MCP_REQUIRED_SCOPES="notebooklm:access"

notebooklm-mcp-remote
```

This serves the MCP endpoint at:

```text
https://notebooklm.example.com/mcp
```

If you want the process itself to terminate TLS instead of using a reverse proxy:

```bash
export NOTEBOOKLM_MCP_TLS_CERTFILE=/path/to/fullchain.pem
export NOTEBOOKLM_MCP_TLS_KEYFILE=/path/to/privkey.pem
notebooklm-mcp-remote
```

### Required environment variables

- `NOTEBOOKLM_MCP_PUBLIC_URL`
  - Public base URL for the server, for example `https://notebooklm.example.com`
  - Must be HTTPS outside localhost
- `NOTEBOOKLM_MCP_OAUTH_PASSWORD`
  - Password shown on the local consent screen when approving a new MCP client

### Optional environment variables

- `NOTEBOOKLM_MCP_HOST`
- `NOTEBOOKLM_MCP_PORT`
- `NOTEBOOKLM_MCP_REQUIRED_SCOPES`
- `NOTEBOOKLM_MCP_SERVICE_DOCUMENTATION_URL`
- `NOTEBOOKLM_MCP_OAUTH_STORE_PATH`
- `NOTEBOOKLM_MCP_ACCESS_TOKEN_TTL_SECONDS`
- `NOTEBOOKLM_MCP_REFRESH_TOKEN_TTL_SECONDS`
- `NOTEBOOKLM_MCP_AUTHORIZATION_CODE_TTL_SECONDS`
- `NOTEBOOKLM_MCP_CLIENT_SECRET_EXPIRY_SECONDS`
- `NOTEBOOKLM_MCP_TLS_CERTFILE`
- `NOTEBOOKLM_MCP_TLS_KEYFILE`

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

On first connect, the client should:

1. Hit `/mcp`
2. Receive an OAuth challenge with protected-resource metadata
3. Discover the server OAuth metadata
4. Register dynamically
5. Open the browser to `/authorize`
6. Land on the local consent screen
7. Exchange the authorization code for tokens

## Endpoints

- MCP: `/mcp`
- Health: `/health`
- Health alias: `/healthz`
- OAuth metadata: `/.well-known/oauth-authorization-server`
- Protected resource metadata: `/.well-known/oauth-protected-resource/mcp`
- Authorization endpoint: `/authorize`
- Token endpoint: `/token`
- Dynamic registration: `/register`
- Revocation: `/revoke`
- Consent UI: `/oauth/consent`

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

## Development

```bash
ruff format src tests
ruff check src tests
pytest
```
