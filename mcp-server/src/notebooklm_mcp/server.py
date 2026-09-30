"""NotebookLM MCP server."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.caching import CacheHint
from mcp.server.connection import MODERN_PROTOCOL_VERSIONS
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, GetPromptResult, InputRequiredResult
from notebooklm import NotebookLMClient
from notebooklm.rpc import AudioFormat, AudioLength
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

# Mirrors the CLI's kebab-case choices (src/notebooklm/cli/generate_cmd.py) so
# tool callers and CLI users describe audio style the same way.
_AUDIO_FORMAT_CHOICES: dict[str, AudioFormat] = {
    "deep-dive": AudioFormat.DEEP_DIVE,
    "brief": AudioFormat.BRIEF,
    "critique": AudioFormat.CRITIQUE,
    "debate": AudioFormat.DEBATE,
}
_AUDIO_LENGTH_CHOICES: dict[str, AudioLength] = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG,
}


def _require_audio_choice(choices: dict[str, Any], value: str, *, flag: str) -> Any:
    try:
        return choices[value]
    except KeyError:
        allowed = ", ".join(choices)
        raise ValueError(f"{flag} must be one of: {allowed}") from None


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

Remote deployments use Streamable HTTP and accept only bearer tokens issued
by the configured OIDC provider. The server itself uses your existing local
NotebookLM login state.
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


# "Resource not found" in the 2025-11-25 era. The 2026-07-28 era moved it to
# -32602 (INVALID_PARAMS); mcp 2.x sends -32602 in both eras, so legacy
# clients get -32002 from NotebookLMServer.read_resource below.
RESOURCE_NOT_FOUND = -32002

# 2026-07-28 freshness hints. The tool, prompt and resource lists are code
# constants, identical for every caller and changed only by a deploy, so a
# client may reuse them for 5 minutes. "private" because every remote request is
# OAuth-scoped: a shared cache must not serve one principal's result to another.
# resources/read has no hint (SDK default: 0 ms, private) because both resources
# are live NotebookLM data.
LIST_CACHE_HINT = CacheHint(ttl_ms=300_000, scope="private")
CACHE_HINTS = {
    "server/discover": LIST_CACHE_HINT,
    "tools/list": LIST_CACHE_HINT,
    "prompts/list": LIST_CACHE_HINT,
    "resources/list": LIST_CACHE_HINT,
    "resources/templates/list": LIST_CACHE_HINT,
}

ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "notebook.jovanovic.org.uk"]
# DNS-rebinding protection treats an *unset* allowed_origins as "reject every
# request that carries an Origin header" (see
# mcp.server.transport_security._validate_origin) rather than "no restriction"
# — only requests with no Origin header at all (same-origin navigations, curl,
# most non-browser MCP clients) pass through un-checked. Browser-based MCP
# clients (the claude.ai connector UI) always send Origin on cross-origin POSTs,
# so without this the OAuth dance can complete and still have every real /mcp
# call rejected with 403 "Invalid Origin header" — a token being valid never
# matters if this check fires first.
ALLOWED_ORIGINS = [
    "https://claude.ai",
    "https://notebook.jovanovic.org.uk",
    "http://localhost:*",
    "http://127.0.0.1:*",
]


class NotebookLMServer(MCPServer):
    """MCPServer with this deployment's transport security and not-found codes.

    mcp 2.x takes transport security per HTTP app rather than in the
    constructor, so streamable_http_app() fills it in here: every caller
    (remote.py, tests, `run`) gets the same host and origin allow-lists.

    Unknown resources and prompts are rejected before any function runs. The
    checks only match URIs and names against what is registered; they never
    call a resource function (the notebook template calls Google).
    """

    def __init__(self, *args: Any, host: str = "127.0.0.1", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http_host = host
        self._transport_security = TransportSecuritySettings(
            allowed_hosts=ALLOWED_HOSTS, allowed_origins=ALLOWED_ORIGINS
        )

    def streamable_http_app(self, **kwargs: Any) -> Starlette:
        # run_streamable_http_async passes transport_security=None explicitly,
        # so replace None rather than only filling a missing key.
        if kwargs.get("transport_security") is None:
            kwargs["transport_security"] = self._transport_security
        kwargs.setdefault("host", self._http_host)
        return super().streamable_http_app(**kwargs)

    async def read_resource(
        self, uri: AnyUrl | str, context: Context[Any, Any] | None = None
    ) -> Iterable[ReadResourceContents] | InputRequiredResult:
        uri_str = str(uri)
        manager = self._resource_manager
        known = any(str(r.uri) == uri_str for r in manager.list_resources()) or any(
            t.matches(uri_str) is not None for t in manager.list_templates()
        )
        if not known:
            modern = context is not None and context.protocol_version in MODERN_PROTOCOL_VERSIONS
            raise MCPError(
                code=INVALID_PARAMS if modern else RESOURCE_NOT_FOUND,
                message=f"Resource not found: {uri_str}",
                data={"uri": uri_str},
            )
        return await super().read_resource(uri, context)

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        context: Context[Any, Any] | None = None,
    ) -> GetPromptResult | InputRequiredResult:
        if self._prompt_manager.get_prompt(name) is None:
            raise MCPError(code=INVALID_PARAMS, message=f"Unknown prompt: {name}")
        return await super().get_prompt(name, arguments, context)


def create_mcp_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8006,
    auth_settings: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
) -> NotebookLMServer:
    """Create the MCP server for stdio or remote HTTP transports.

    `port` is only used by `main` when running Streamable HTTP directly.
    """

    client_manager = NotebookLMClientManager()

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]):
        try:
            yield
        finally:
            await client_manager.close()

    mcp = NotebookLMServer(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        website_url=SERVER_WEBSITE_URL,
        host=host,
        auth=auth_settings,
        token_verifier=token_verifier,
        lifespan=lifespan,
        cache_hints=CACHE_HINTS,
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
                    "resource_metadata": "/.well-known/oauth-protected-resource/mcp"
                    if auth_settings
                    else None,
                },
            }
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
    async def generate_artifact(
        notebook_id: str,
        artifact_type: str,
        instructions: str | None = None,
        audio_format: str | None = None,
        audio_length: str | None = None,
    ) -> dict[str, Any]:
        """Start generating an audio, video, or report artifact.

        instructions: free-text customization prompt (e.g. "casual, energetic
        two-host banter debating the differentials, like an EM case review").
        audio_format (audio only): one of deep-dive, brief, critique, debate.
        audio_length (audio only): one of short, default, long.
        """

        client = await _client()
        if artifact_type == "audio":
            format_value = (
                _require_audio_choice(_AUDIO_FORMAT_CHOICES, audio_format, flag="audio_format")
                if audio_format is not None
                else None
            )
            length_value = (
                _require_audio_choice(_AUDIO_LENGTH_CHOICES, audio_length, flag="audio_length")
                if audio_length is not None
                else None
            )
            status = await client.artifacts.generate_audio(
                notebook_id,
                instructions=instructions,
                audio_format=format_value,
                audio_length=length_value,
            )
        elif artifact_type == "video":
            status = await client.artifacts.generate_video(notebook_id, instructions=instructions)
        elif artifact_type == "report":
            status = await client.artifacts.generate_report(notebook_id, custom_prompt=instructions)
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
        choices=["stdio", "streamable-http"],
        help="Transport to run. HTTP+SSE was removed; use 'streamable-http'.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8006)
    args = parser.parse_args()

    mcp = create_mcp_server(host=args.host, port=args.port)
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
