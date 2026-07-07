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
- **URL**: https://notebooklm-mcp.jovanovic.org.uk
- **Local**: http://127.0.0.1:8005
- **Systemd**: `notebooklm-mcp.service`
- **Tunnel**: `cloudflared-notebooklm-mcp.service`

**Transport**: SSE (Server-Sent Events) for HTTPS MCP protocol

## Management Commands

### View Status
```bash
systemctl --user status notebooklm-api.service
systemctl --user status notebooklm-mcp.service
systemctl --user status cloudflared-notebooklm.service
systemctl --user status cloudflared-notebooklm-mcp.service
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
journalctl --user -u cloudflared-notebooklm-mcp.service -f
```

## Claude Configuration

Add to `~/.claude/settings.json` or `.mcp.json`:

```json
{
  "mcpServers": {
    "notebooklm": {
      "transport": "sse",
      "url": "https://notebooklm-mcp.jovanovic.org.uk/sse",
      "timeout": 30
    }
  }
}
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
├── cloudflared-notebooklm.service
└── cloudflared-notebooklm-mcp.service

~/.cloudflared/
├── notebooklm-api.yml
├── notebooklm-api.json
├── notebooklm-mcp.yml
└── notebooklm-mcp.json

~/projects/notebooklm-py/
├── backend/          # REST API (FastAPI)
└── mcp-server/       # MCP Server (FastMCP + SSE)
```

## Cloudflare DNS Records

| Subdomain | Target | Purpose |
|-----------|--------|---------|
| `notebooklm-api` | Tunnel ID | REST API |
| `notebooklm-mcp` | Tunnel ID | MCP Server |

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
              notebooklm-api    notebooklm-mcp
                     |                 |
                  (FastAPI)          (SSE/MCP)
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
