"""Remote Streamable HTTP entrypoint for NotebookLM MCP."""

from __future__ import annotations

import logging
import os

import uvicorn
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from starlette.middleware.cors import CORSMiddleware

from .config import RemoteServerConfig
from .oauth import FileBackedOAuthProvider
from .server import create_mcp_server

logger = logging.getLogger(__name__)

OAUTH_DISABLED = os.getenv("OAUTH_DISABLED", "").lower() in ("true", "1")


def build_auth_settings(config: RemoteServerConfig) -> AuthSettings:
    """Build FastMCP auth settings from environment config."""

    return AuthSettings(
        issuer_url=config.issuer_url,
        service_documentation_url=config.service_documentation_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            client_secret_expiry_seconds=config.client_secret_expiry_seconds,
            valid_scopes=list(config.required_scopes),
            default_scopes=list(config.required_scopes),
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=list(config.required_scopes),
        resource_server_url=config.resource_server_url,
    )


def build_asgi_app(mcp) -> CORSMiddleware:
    """Wrap the FastMCP Starlette app with CORS covering every route.

    FastMCP's ``streamable_http_app()`` only wires CORS onto the OAuth
    routes (register/authorize/token/revoke) via the mcp SDK's own per-route
    ``cors_middleware()`` — the ``/mcp`` route itself is wrapped directly by
    ``RequireAuthMiddleware`` with no CORS handling. A browser OPTIONS
    preflight to ``/mcp`` therefore hits the auth check first (no
    Authorization header on a preflight) and gets a bare 401 with no
    ``Access-Control-Allow-*`` headers, which browser MCP clients (e.g. the
    claude.ai connector UI) silently treat as connection failure even though
    a server-to-server call with a valid token succeeds fine. Wrapping the
    whole app in ``CORSMiddleware`` intercepts preflights before
    ``RequireAuthMiddleware`` ever sees them.

    ``allow_origins="*"`` mirrors the mcp SDK's own ``cors_middleware()``
    (used for /register, /authorize, /token, /revoke) and is safe here
    because auth is a bearer token attached explicitly by the client, not
    an ambient credential like a cookie — ``allow_credentials`` is left at
    its default (False), so a wildcard origin cannot be combined with
    cookie-based access even if one were added later. Methods/headers are
    scoped to exactly what the streamable-http transport and OAuth bearer
    auth use, rather than wildcarded, since those don't need to be broad
    the way the origin does (MCP clients aren't running from a fixed set
    of known origins).
    """

    return CORSMiddleware(
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


def main() -> None:
    """Run the MCP server over Streamable HTTP with OAuth 2.1 enabled."""

    try:
        config = RemoteServerConfig.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if OAUTH_DISABLED:
        logger.info("OAuth DISABLED (OAUTH_DISABLED=true) — no auth on MCP endpoints")
        mcp = create_mcp_server(
            host=config.host,
            port=config.port,
        )
    else:
        auth_provider = FileBackedOAuthProvider(config)
        mcp = create_mcp_server(
            host=config.host,
            port=config.port,
            auth_settings=build_auth_settings(config),
            auth_provider=auth_provider,
            oauth_password=config.oauth_password,
            trusted_access_emails=config.trusted_access_emails,
        )

    logger.info("NotebookLM MCP resource URL: %s", config.resource_server_url)
    logger.info("NotebookLM MCP issuer URL: %s", config.issuer_url)

    # workers=1 (uvicorn's default when unset) is load-bearing here, not
    # just a performance choice: FileBackedOAuthProvider guards its state
    # with an in-process asyncio.Lock and read-modify-write's a single JSON
    # file. Multiple worker processes would each keep their own in-memory
    # copy and race on the file, silently losing concurrent token issuance/
    # revocation. Do not add --workers/-w > 1 to this entrypoint without
    # first moving the OAuth state to a real datastore.
    uvicorn.run(
        build_asgi_app(mcp),
        host=config.host,
        port=config.port,
        log_level="info",
        workers=1,
        ssl_certfile=str(config.tls_certfile) if config.tls_certfile else None,
        ssl_keyfile=str(config.tls_keyfile) if config.tls_keyfile else None,
    )


if __name__ == "__main__":
    main()
