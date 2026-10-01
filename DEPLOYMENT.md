# NotebookLM Deployment Summary

## Services Deployed

All services are running via systemd and auto-start on boot (lingering enabled).

### 1. NotebookLM REST API
- **URL**: https://notebooklm-api.jovanovic.org.uk
- **Local**: http://127.0.0.1:8004
- **Systemd**: `notebooklm-api.service`
- **Tunnel**: `cloudflared-notebooklm.service`

**Endpoints**:
- Health: `/api/v1/health`
- Swagger UI: `/docs`
- 30+ REST API endpoints for notebooks, sources, chat, artifacts

### 2. NotebookLM MCP Server
- **URL**: https://notebook.jovanovic.org.uk/mcp
- **Local**: http://127.0.0.1:8005
- **Systemd**: `notebooklm-mcp.service` (runs `mcp-server/.venv/bin/notebooklm-mcp-remote`)
- **Proxy**: Cloudflare (proxied DNS) to Caddy's `notebook.jovanovic.org.uk` site, which forwards to `localhost:8005`. There is no MCP tunnel.

**Transport**: Streamable HTTP with OAuth 2.1. HTTP+SSE has been removed. See
[mcp-server/README.md](mcp-server/README.md) for supported MCP protocol versions.

## Management Commands

### View Status
```bash
systemctl --user status notebooklm-api.service
systemctl --user status notebooklm-mcp.service
systemctl --user status cloudflared-notebooklm.service
```

### Restart Services
```bash
systemctl --user restart notebooklm-api.service
systemctl --user restart notebooklm-mcp.service
```

### View Logs
```bash
journalctl --user -u notebooklm-api.service -f
journalctl --user -u notebooklm-mcp.service -f
journalctl --user -u cloudflared-notebooklm.service -f
```

## Claude Configuration

Add the remote server over HTTP. Claude Code runs the OAuth flow on first use:

```bash
claude mcp add --transport http notebooklm https://notebook.jovanovic.org.uk/mcp
```

## MCP Tools Available

| Tool | Description |
|------|-------------|
| `list_notebooks` | List all notebooks |
| `create_notebook` | Create new notebook |
| `get_notebook` | Get notebook details |
| `list_sources` | List notebook sources |
| `add_source` | Add URL/text/YouTube/file |
| `ask_question` | Query notebook AI |
| `generate_artifact` | Generate audio/video/report |
| `get_artifact_status` | Check generation status |

## Resources

- `notebooklm://notebooks` - All notebooks
- `notebooklm://notebooks/{id}` - Specific notebook

## File Locations

```
~/.config/systemd/user/
├── notebooklm-api.service
├── notebooklm-mcp.service
└── cloudflared-notebooklm.service

~/.cloudflared/
├── notebooklm-api.yml
└── notebooklm-api.json

/etc/caddy/Caddyfile   # notebook.jovanovic.org.uk site for the MCP server

~/projects/notebooklm-py/
├── backend/          # REST API (FastAPI)
└── mcp-server/       # MCP Server (FastMCP, Streamable HTTP)
```

## Cloudflare DNS Records

| Subdomain | Target | Purpose |
|-----------|--------|---------|
| `notebooklm-api` | Tunnel ID | REST API |
| `notebook` | Proxied to this host (Caddy) | MCP Server |

## Troubleshooting

### Services not starting
```bash
journalctl --user -u <service-name> -n 50
```

### Tunnel connection issues
```bash
cloudflared tunnel info <tunnel-id>
```

### Check authentication
```bash
notebooklm status
```

## Architecture

```
                    Cloudflare (HTTPS/TLS)
                              |
                     +--------+--------+
                     |                 |
              notebooklm-api       notebook
                 (tunnel)          (Caddy)
                     |                 |
                  (FastAPI)   (Streamable HTTP MCP)
                     |                 |
                127.0.0.1:8004   127.0.0.1:8005
```

## Next Steps

1. **Test MCP with Claude**: Add the config and test tools
2. **Build applications**: Use the REST API for integrations
3. **Monitor logs**: `journalctl --user -f` to watch all services

## Authentication

Both services use your existing `notebooklm login` credentials.
Run `notebooklm login` if authentication expires.
