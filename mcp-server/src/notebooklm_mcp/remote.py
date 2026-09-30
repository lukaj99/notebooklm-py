"""Remote Streamable HTTP entrypoint for NotebookLM MCP."""

from __future__ import annotations

import json
import logging

import uvicorn
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from .auth import REQUIRED_SCOPE, PocketIdTokenVerifier
from .config import RemoteServerConfig
from .server import create_mcp_server

logger = logging.getLogger(__name__)


def build_auth_settings(config: RemoteServerConfig) -> AuthSettings:
    """Build MCP auth settings: Pocket ID is the authorization server."""

    return AuthSettings(
        issuer_url=config.issuer,
        required_scopes=[REQUIRED_SCOPE],
        resource_server_url=config.resource_url,
        # The token verifier checks `aud` itself (and the SDK's resource
        # check reads a field it sets to the same value), so leave the
        # SDK-side check off rather than run two that can disagree.
        validate_token_resource=False,
    )


def protected_resource_metadata(config: RemoteServerConfig) -> dict[str, object]:
    """RFC 9728 document naming Pocket ID as the authorization server."""

    return {
        "resource": config.resource_url,
        "authorization_servers": [config.issuer],
        "scopes_supported": [REQUIRED_SCOPE],
        "bearer_methods_supported": ["header"],
    }


class Mcp400DiagnosticMiddleware:
    """Log method/headers/body for any non-2xx response to POST /mcp.

    Added to pin down a racing first-connect failure seen from some MCP
    clients (incl. Claude Desktop): two near-simultaneous POST /mcp requests
    on a fresh connection, one of which 400s before the other succeeds.
    Only fires on non-2xx so normal traffic bodies are never logged.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/mcp" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        status_holder: dict[str, int] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        status = status_holder.get("status")
        if status is not None and status >= 400:
            headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.decode("latin-1").lower()
                in (
                    "accept",
                    "content-type",
                    "mcp-session-id",
                    "mcp-protocol-version",
                    "authorization",
                )
            }
            if "authorization" in headers:
                headers["authorization"] = "***redacted***"
            logger.warning(
                "POST /mcp -> %s; headers=%s; body=%s",
                status,
                headers,
                b"".join(body_chunks)[:2000],
            )


class ProtectedResourceMetadataMiddleware:
    """Serve RFC 9728 metadata at the bare and the resource-suffixed path.

    RFC 9728 §3.1 puts the document at
    ``/.well-known/oauth-protected-resource/mcp``, which the 401 challenge
    points at. The claude.ai connector also requests the bare path directly
    without following that hint (every attempt in live logs), so both serve
    the same document. It is rendered here from the config rather than by
    the SDK's model so ``authorization_servers`` carries the issuer
    verbatim: clients compare it to Pocket ID's issuer as an exact string,
    and URL types append a ``/`` to a path-less URL.
    """

    PATHS = frozenset(
        {"/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}
    )

    def __init__(self, app, metadata: dict[str, object]):
        self.app = app
        self.metadata_json = json.dumps(metadata).encode()

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["path"] in self.PATHS
            and scope["method"] in ("GET", "OPTIONS")
        ):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"public, max-age=3600"),
                        (b"access-control-allow-origin", b"*"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": self.metadata_json})
            return
        await self.app(scope, receive, send)


def build_asgi_app(mcp, config: RemoteServerConfig | None = None) -> ASGIApp:
    """Wrap the MCP Starlette app with CORS covering every route.

    The SDK's ``streamable_http_app()`` wraps the ``/mcp`` route directly in
    ``RequireAuthMiddleware`` with no CORS handling. A browser OPTIONS
    preflight to ``/mcp`` therefore hits the auth check first (no
    Authorization header on a preflight) and gets a bare 401 with no
    ``Access-Control-Allow-*`` headers, which browser MCP clients (e.g. the
    claude.ai connector UI) silently treat as connection failure even though
    a server-to-server call with a valid token succeeds fine. Wrapping the
    whole app in ``CORSMiddleware`` intercepts preflights before
    ``RequireAuthMiddleware`` ever sees them.

    ``allow_origins="*"`` mirrors the mcp SDK's own ``cors_middleware()``
    and is safe here
    because auth is a bearer token attached explicitly by the client, not
    an ambient credential like a cookie — ``allow_credentials`` is left at
    its default (False), so a wildcard origin cannot be combined with
    cookie-based access even if one were added later. Methods/headers are
    scoped to exactly what the streamable-http transport and OAuth bearer
    auth use, rather than wildcarded, since those don't need to be broad
    the way the origin does (MCP clients aren't running from a fixed set
    of known origins).
    """

    app: ASGIApp = CORSMiddleware(
        mcp.streamable_http_app(),
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "mcp-protocol-version",
            "mcp-session-id",
            "last-event-id",
        ],
    )

    app = Mcp400DiagnosticMiddleware(app)

    if config is not None:
        app = ProtectedResourceMetadataMiddleware(app, protected_resource_metadata(config))

    return app


def build_remote_app(
    config: RemoteServerConfig, *, token_verifier: TokenVerifier | None = None
) -> ASGIApp:
    """The full remote app: MCP server behind Pocket ID bearer-token auth."""

    verifier = token_verifier or PocketIdTokenVerifier(
        issuer=config.issuer,
        resource_url=config.resource_url,
        allowed_subs=config.allowed_subs,
    )
    mcp = create_mcp_server(
        host=config.host,
        port=config.port,
        auth_settings=build_auth_settings(config),
        token_verifier=verifier,
    )
    return build_asgi_app(mcp, config)


def main() -> None:
    """Run the MCP server over Streamable HTTP as a Pocket ID resource server."""

    try:
        config = RemoteServerConfig.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    logger.info("NotebookLM MCP resource URL: %s", config.resource_url)
    logger.info("NotebookLM MCP authorization server: %s", config.issuer)

    uvicorn.run(
        build_remote_app(config),
        host=config.host,
        port=config.port,
        log_level="info",
        workers=1,
        ssl_certfile=str(config.tls_certfile) if config.tls_certfile else None,
        ssl_keyfile=str(config.tls_keyfile) if config.tls_keyfile else None,
    )


if __name__ == "__main__":
    main()
