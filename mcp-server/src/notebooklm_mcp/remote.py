"""Remote Streamable HTTP entrypoint for NotebookLM MCP."""

from __future__ import annotations

import logging
import os

import uvicorn
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

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

    uvicorn.run(
        mcp.streamable_http_app(),
        host=config.host,
        port=config.port,
        log_level="info",
        ssl_certfile=str(config.tls_certfile) if config.tls_certfile else None,
        ssl_keyfile=str(config.tls_keyfile) if config.tls_keyfile else None,
    )


if __name__ == "__main__":
    main()
