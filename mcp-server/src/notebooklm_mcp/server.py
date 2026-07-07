"""NotebookLM MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.fastmcp import FastMCP
from notebooklm import NotebookLMClient
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .oauth import FileBackedOAuthProvider, PendingAuthorization

# Patch FastMCP default token expiry to 365 days for self-hosted use.
# The in-memory provider defaults to 1 hour, which causes "Connection expired" errors.
try:
    import fastmcp.server.auth.providers.in_memory as _in_memory_auth
    _in_memory_auth.DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS = 365 * 24 * 3600
except (ImportError, AttributeError):
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_NAME = "notebooklm"
SERVER_WEBSITE_URL = "https://github.com/teng-lin/notebooklm-py"
SERVER_INSTRUCTIONS = """Google NotebookLM MCP Server

This server provides tools to interact with Google NotebookLM:
- Create and inspect notebooks
- Add and list sources
- Ask questions against notebook contents
- Generate NotebookLM artifacts

Remote deployments use Streamable HTTP plus OAuth 2.1 authorization code flow
with PKCE. The server itself uses your existing local NotebookLM login state.
"""


class NotebookLMClientManager:
    """Lazy lifecycle management for the shared NotebookLM client."""

    def __init__(self) -> None:
        self._client: NotebookLMClient | None = None
        self._lock = asyncio.Lock()

    async def get_client(self) -> NotebookLMClient:
        async with self._lock:
            if self._client is None:
                client = await NotebookLMClient.from_storage()
                self._client = await client.__aenter__()
            return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.__aexit__(None, None, None)
                self._client = None


def _serialize_notebook(notebook: Any) -> dict[str, Any]:
    return {
        "id": notebook.id,
        "title": notebook.title,
        "created_at": notebook.created_at.isoformat() if notebook.created_at else None,
        "is_owner": notebook.is_owner,
        "sources_count": getattr(notebook, "sources_count", None),
    }


def _serialize_source(source: Any) -> dict[str, Any]:
    return {
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "kind": source.kind.value,
        "status": source.status,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }


def _serialize_reference(reference: Any) -> dict[str, Any]:
    return {
        "source_id": reference.source_id,
        "citation_number": reference.citation_number,
        "cited_text": reference.cited_text,
        "start_char": reference.start_char,
        "end_char": reference.end_char,
        "chunk_id": reference.chunk_id,
    }


def _serialize_generation_status(status: Any, artifact_type: str | None = None) -> dict[str, Any]:
    payload = {
        "task_id": status.task_id,
        "artifact_id": status.task_id,
        "status": status.status,
        "url": status.url,
        "error": status.error,
        "error_code": status.error_code,
        "metadata": status.metadata,
    }
    if artifact_type is not None:
        payload["artifact_type"] = artifact_type
    return payload


async def _add_source_for_type(
    client: NotebookLMClient,
    *,
    notebook_id: str,
    source_type: str,
    content: str,
    title: str | None,
) -> Any:
    """Dispatch source creation to the correct NotebookLM client method."""

    if source_type == "url":
        return await client.sources.add_url(notebook_id, content)
    if source_type == "text":
        return await client.sources.add_text(notebook_id, title or "Text source", content)
    if source_type == "youtube":
        # notebooklm-py auto-detects YouTube URLs via add_url()
        return await client.sources.add_url(notebook_id, content)
    if source_type == "file":
        return await client.sources.add_file(notebook_id, Path(content).expanduser())

    raise ValueError("source_type must be one of: url, text, youtube, file")


def _render_consent_page(
    pending: PendingAuthorization,
    client_name: str,
    resource_url: str | None,
    error: str | None = None,
    require_password: bool = True,
    authenticated_email: str | None = None,
) -> str:
    scopes_html = "".join(
        f"<li><code>{html.escape(scope)}</code></li>" for scope in (pending.scopes or [])
    )
    error_html = (
        f"<p style='color:#b42318;background:#fef3f2;padding:12px;border-radius:8px;'>"
        f"{html.escape(error)}</p>"
        if error
        else ""
    )
    resource_html = (
        f"<p><strong>Resource:</strong> <code>{html.escape(resource_url)}</code></p>"
        if resource_url
        else ""
    )
    identity_html = (
        f"<p><strong>Cloudflare Access identity:</strong> <code>{html.escape(authenticated_email)}</code></p>"
        if authenticated_email
        else ""
    )
    password_html = (
        """
        <label for="password"><strong>Owner password</strong></label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        """
        if require_password
        else "<p>Cloudflare Access has already authenticated this approval request.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize NotebookLM MCP</title>
    <style>
      body {{
        margin: 0;
        background: #f7f8fb;
        color: #111827;
        font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        max-width: 640px;
        margin: 48px auto;
        background: white;
        border-radius: 16px;
        padding: 32px;
        box-shadow: 0 20px 50px rgba(15, 23, 42, 0.08);
      }}
      h1 {{ margin-top: 0; }}
      code {{
        background: #f1f5f9;
        padding: 0.15rem 0.35rem;
        border-radius: 6px;
      }}
      input[type=password] {{
        width: 100%;
        padding: 12px;
        border: 1px solid #cbd5e1;
        border-radius: 10px;
        box-sizing: border-box;
        margin: 12px 0 20px;
      }}
      button {{
        border: 0;
        border-radius: 10px;
        padding: 12px 18px;
        cursor: pointer;
        font-size: 14px;
      }}
      button.primary {{
        background: #0f766e;
        color: white;
      }}
      button.secondary {{
        background: #e2e8f0;
        color: #0f172a;
        margin-left: 8px;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Authorize NotebookLM MCP</h1>
      <p><strong>Client:</strong> {html.escape(client_name)}</p>
      {resource_html}
      {identity_html}
      <p><strong>Requested scopes:</strong></p>
      <ul>{scopes_html or "<li><em>No scopes requested</em></li>"}</ul>
      {error_html}
      <form method="post">
        <input type="hidden" name="grant_id" value="{html.escape(pending.grant_id)}">
        {password_html}
        <button class="primary" type="submit" name="action" value="approve">Approve</button>
        <button class="secondary" type="submit" name="action" value="deny">Deny</button>
      </form>
    </main>
  </body>
</html>
"""


def create_mcp_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8006,
    auth_settings: AuthSettings | None = None,
    auth_provider: FileBackedOAuthProvider | None = None,
    oauth_password: str | None = None,
    trusted_access_emails: tuple[str, ...] = (),
) -> FastMCP:
    """Create a FastMCP server for stdio or remote HTTP transports."""

    client_manager = NotebookLMClientManager()

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]):
        try:
            yield
        finally:
            await client_manager.close()

    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        website_url=SERVER_WEBSITE_URL,
        host=host,
        port=port,
        
        sse_path="/mcp",
        message_path="/mcp/messages",
        auth=auth_settings,
        auth_server_provider=auth_provider,
        lifespan=lifespan,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "notebook.jovanovic.org.uk"],
        ),
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health_check(_: Request):
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request):
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def root(_: Request):
        return JSONResponse(
            {
                "name": SERVER_NAME,
                "transport": "streamable-http",
                "oauth_enabled": auth_settings is not None,
                "endpoints": {
                    "mcp": "/mcp",
                    "health": "/health",
                    "authorize": "/authorize" if auth_settings else None,
                    "oauth_metadata": "/.well-known/oauth-authorization-server"
                    if auth_settings
                    else None,
                    "resource_metadata": "/.well-known/oauth-protected-resource/mcp"
                    if auth_settings
                    else None,
                },
            }
        )

    if auth_provider is not None and oauth_password is not None:
        trusted_access_emails_set = {email.lower() for email in trusted_access_emails}

        def _trusted_access_email(request: Request) -> str | None:
            if not trusted_access_emails_set:
                return None
            email = request.headers.get("cf-access-authenticated-user-email")
            if email is None:
                return None
            normalized = email.strip().lower()
            if normalized in trusted_access_emails_set:
                return normalized
            return None

        @mcp.custom_route("/oauth/consent", methods=["GET", "POST"], include_in_schema=False)
        async def oauth_consent(request: Request):
            if request.method == "GET":
                grant_id = request.query_params.get("grant_id")
                if not grant_id:
                    return HTMLResponse("Missing grant_id", status_code=400)

                pending = await auth_provider.get_pending_authorization(grant_id)
                if pending is None:
                    return HTMLResponse(
                        "Authorization request expired or not found", status_code=404
                    )

                client = await auth_provider.get_client(pending.client_id)
                client_name = (
                    client.client_name or client.client_id or "unknown-client"
                    if client
                    else "unknown-client"
                )
                return HTMLResponse(
                    _render_consent_page(
                        pending=pending,
                        client_name=client_name,
                        resource_url=pending.resource,
                        require_password=not trusted_access_emails_set,
                        authenticated_email=_trusted_access_email(request),
                    )
                )

            form = await request.form()
            grant_id_raw = form.get("grant_id")
            action_raw = form.get("action")
            password_raw = form.get("password")

            grant_id = grant_id_raw if isinstance(grant_id_raw, str) else ""
            action = action_raw if isinstance(action_raw, str) else "approve"
            password = password_raw if isinstance(password_raw, str) else ""

            pending = await auth_provider.get_pending_authorization(grant_id)
            if pending is None:
                return HTMLResponse("Authorization request expired or not found", status_code=404)

            client = await auth_provider.get_client(pending.client_id)
            client_name = (
                client.client_name or client.client_id or "unknown-client"
                if client
                else "unknown-client"
            )
            authenticated_email = _trusted_access_email(request)

            if action == "deny":
                redirect_url = await auth_provider.deny_pending_authorization(grant_id)
                if redirect_url is None:
                    return HTMLResponse(
                        "Authorization request expired or not found", status_code=404
                    )
                return RedirectResponse(
                    redirect_url, status_code=302, headers={"Cache-Control": "no-store"}
                )

            if trusted_access_emails_set:
                if authenticated_email is None:
                    return HTMLResponse(
                        _render_consent_page(
                            pending=pending,
                            client_name=client_name,
                            resource_url=pending.resource,
                            error="Cloudflare Access authentication required",
                            require_password=False,
                            authenticated_email=None,
                        ),
                        status_code=403,
                    )
            elif not hmac.compare_digest(password, oauth_password):
                return HTMLResponse(
                    _render_consent_page(
                        pending=pending,
                        client_name=client_name,
                        resource_url=pending.resource,
                        error="Incorrect password",
                        require_password=True,
                    ),
                    status_code=403,
                )

            redirect_url = await auth_provider.approve_pending_authorization(grant_id)
            if redirect_url is None:
                return HTMLResponse("Authorization request expired or not found", status_code=404)

            return RedirectResponse(
                redirect_url, status_code=302, headers={"Cache-Control": "no-store"}
            )

    async def _client() -> NotebookLMClient:
        return await client_manager.get_client()

    @mcp.tool()
    async def list_notebooks() -> list[dict[str, Any]]:
        """List all notebooks available in the authenticated NotebookLM account."""

        client = await _client()
        notebooks = await client.notebooks.list()
        return [_serialize_notebook(notebook) for notebook in notebooks]

    @mcp.tool()
    async def create_notebook(title: str) -> dict[str, Any]:
        """Create a new NotebookLM notebook."""

        client = await _client()
        notebook = await client.notebooks.create(title)
        return _serialize_notebook(notebook)

    @mcp.tool()
    async def get_notebook(notebook_id: str) -> dict[str, Any]:
        """Get notebook metadata plus its current sources."""

        client = await _client()
        notebook, sources = await asyncio.gather(
            client.notebooks.get(notebook_id),
            client.sources.list(notebook_id),
        )
        payload = _serialize_notebook(notebook)
        payload["sources"] = [_serialize_source(source) for source in sources]
        return payload

    @mcp.tool()
    async def list_sources(notebook_id: str) -> list[dict[str, Any]]:
        """List sources for a specific notebook."""

        client = await _client()
        sources = await client.sources.list(notebook_id)
        return [_serialize_source(source) for source in sources]

    @mcp.tool()
    async def add_source(
        notebook_id: str,
        source_type: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Add a NotebookLM source from a URL, text block, YouTube URL, or local file path."""

        client = await _client()
        source = await _add_source_for_type(
            client,
            notebook_id=notebook_id,
            source_type=source_type,
            content=content,
            title=title,
        )
        return _serialize_source(source)

    @mcp.tool()
    async def ask_question(notebook_id: str, question: str) -> dict[str, Any]:
        """Ask NotebookLM a question about one notebook."""

        client = await _client()
        result = await client.chat.ask(notebook_id, question)
        return {
            "answer": result.answer,
            "conversation_id": result.conversation_id,
            "turn_number": result.turn_number,
            "is_follow_up": result.is_follow_up,
            "references": [_serialize_reference(reference) for reference in result.references],
        }

    @mcp.tool()
    async def generate_artifact(notebook_id: str, artifact_type: str) -> dict[str, Any]:
        """Start generating an audio, video, or report artifact."""

        client = await _client()
        if artifact_type == "audio":
            status = await client.artifacts.generate_audio(notebook_id)
        elif artifact_type == "video":
            status = await client.artifacts.generate_video(notebook_id)
        elif artifact_type == "report":
            status = await client.artifacts.generate_report(notebook_id)
        else:
            raise ValueError("artifact_type must be one of: audio, video, report")

        return _serialize_generation_status(status, artifact_type=artifact_type)

    @mcp.tool()
    async def get_artifact_status(notebook_id: str, task_id: str) -> dict[str, Any]:
        """Poll the current status of an in-flight artifact generation task."""

        client = await _client()
        status = await client.artifacts.poll_status(notebook_id, task_id)
        return _serialize_generation_status(status)

    @mcp.resource("notebooklm://notebooks")
    async def notebooks_resource() -> str:
        """Machine-readable JSON dump of all notebooks."""

        client = await _client()
        notebooks = await client.notebooks.list()
        payload = [_serialize_notebook(notebook) for notebook in notebooks]
        return JSONResponse(payload).body.decode()

    @mcp.resource("notebooklm://notebooks/{notebook_id}")
    async def notebook_resource(notebook_id: str) -> str:
        """Machine-readable JSON dump of one notebook and its sources."""

        client = await _client()
        notebook, sources = await asyncio.gather(
            client.notebooks.get(notebook_id),
            client.sources.list(notebook_id),
        )
        payload = _serialize_notebook(notebook)
        payload["sources"] = [_serialize_source(source) for source in sources]
        return JSONResponse(payload).body.decode()

    return mcp


def main() -> None:
    """Run the MCP server with stdio by default."""

    parser = argparse.ArgumentParser(description="NotebookLM MCP server")
    parser.add_argument(
        "transport",
        nargs="?",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport to run. Prefer 'streamable-http' over deprecated 'sse'.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8006)
    args = parser.parse_args()

    if args.transport == "sse":
        logger.warning("SSE is deprecated upstream; prefer streamable-http.")

    mcp = create_mcp_server(host=args.host, port=args.port)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
