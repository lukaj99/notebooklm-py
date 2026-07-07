"""Configuration for the remote NotebookLM MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8006
DEFAULT_SCOPE = "notebooklm:access"
DEFAULT_STORE_PATH = Path("~/.notebooklm/mcp-oauth.json")
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365  # 365 days
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365  # 365 days
DEFAULT_AUTHORIZATION_CODE_TTL_SECONDS = 600


def _parse_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _split_scopes(value: str | None) -> tuple[str, ...]:
    if not value:
        return (DEFAULT_SCOPE,)

    scopes = tuple(scope for scope in value.replace(",", " ").split() if scope)
    if not scopes:
        raise ValueError("NOTEBOOKLM_MCP_REQUIRED_SCOPES cannot be empty")
    return scopes


def _split_emails(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    emails = tuple(
        email.strip().lower() for email in value.replace(";", ",").split(",") if email.strip()
    )
    return tuple(dict.fromkeys(emails))


def _normalize_public_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL must use http or https")
    if not parsed.netloc:
        raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL must include a hostname")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL cannot include params, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL must not include a path")

    is_localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_localhost:
        raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL must use https outside localhost")

    return f"{parsed.scheme}://{parsed.netloc}"


def _expand_path(value: str | Path | None, default: Path | None = None) -> Path | None:
    if value is None:
        if default is None:
            return None
        return default.expanduser()
    return Path(value).expanduser()


@dataclass(slots=True, frozen=True)
class RemoteServerConfig:
    """Environment-driven configuration for remote HTTP deployment."""

    host: str
    port: int
    public_base_url: str
    oauth_password: str
    oauth_store_path: Path
    required_scopes: tuple[str, ...]
    service_documentation_url: str | None
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    authorization_code_ttl_seconds: int
    client_secret_expiry_seconds: int | None
    tls_certfile: Path | None
    tls_keyfile: Path | None
    trusted_access_emails: tuple[str, ...] = ()

    @property
    def issuer_url(self) -> str:
        return self.public_base_url

    @property
    def resource_server_url(self) -> str:
        return f"{self.public_base_url}/mcp"

    @classmethod
    def from_env(cls) -> RemoteServerConfig:
        public_base_url_raw = os.environ.get("NOTEBOOKLM_MCP_PUBLIC_URL")
        if not public_base_url_raw:
            raise ValueError("NOTEBOOKLM_MCP_PUBLIC_URL is required for remote HTTP mode")

        oauth_password = os.environ.get("NOTEBOOKLM_MCP_OAUTH_PASSWORD", "")
        trusted_access_emails = _split_emails(
            os.environ.get("NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS")
        )
        if not oauth_password and not trusted_access_emails:
            raise ValueError(
                "Set NOTEBOOKLM_MCP_OAUTH_PASSWORD or NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS"
            )

        client_secret_expiry_raw = os.environ.get("NOTEBOOKLM_MCP_CLIENT_SECRET_EXPIRY_SECONDS")
        client_secret_expiry_seconds: int | None
        if client_secret_expiry_raw in {None, ""}:
            client_secret_expiry_seconds = None
        else:
            try:
                client_secret_expiry_seconds = int(client_secret_expiry_raw)
            except ValueError as exc:
                raise ValueError(
                    "NOTEBOOKLM_MCP_CLIENT_SECRET_EXPIRY_SECONDS must be an integer"
                ) from exc
            if client_secret_expiry_seconds <= 0:
                raise ValueError(
                    "NOTEBOOKLM_MCP_CLIENT_SECRET_EXPIRY_SECONDS must be greater than 0"
                )

        tls_certfile = _expand_path(os.environ.get("NOTEBOOKLM_MCP_TLS_CERTFILE"))
        tls_keyfile = _expand_path(os.environ.get("NOTEBOOKLM_MCP_TLS_KEYFILE"))
        if (tls_certfile is None) != (tls_keyfile is None):
            raise ValueError(
                "NOTEBOOKLM_MCP_TLS_CERTFILE and NOTEBOOKLM_MCP_TLS_KEYFILE must be set together"
            )

        host = os.environ.get("NOTEBOOKLM_MCP_HOST", DEFAULT_HOST)
        if trusted_access_emails and host not in {"127.0.0.1", "localhost", "::1"}:
            # trusted_access_emails is enforced by trusting the
            # cf-access-authenticated-user-email header, which is only safe
            # if this process is unreachable except through the Cloudflare
            # Access-gated tunnel (which forwards to loopback). Binding
            # anywhere else would let a direct request forge that header
            # and bypass authorization entirely.
            raise ValueError(
                "NOTEBOOKLM_MCP_TRUSTED_ACCESS_EMAILS requires NOTEBOOKLM_MCP_HOST to be "
                "a loopback address (127.0.0.1, localhost, or ::1), since it trusts a "
                "header that only a fronting Cloudflare Access tunnel may set safely"
            )

        return cls(
            host=host,
            port=_parse_int("NOTEBOOKLM_MCP_PORT", DEFAULT_PORT),
            public_base_url=_normalize_public_base_url(public_base_url_raw),
            oauth_password=oauth_password,
            trusted_access_emails=trusted_access_emails,
            oauth_store_path=_expand_path(
                os.environ.get("NOTEBOOKLM_MCP_OAUTH_STORE_PATH"),
                DEFAULT_STORE_PATH,
            )
            or DEFAULT_STORE_PATH.expanduser(),
            required_scopes=_split_scopes(os.environ.get("NOTEBOOKLM_MCP_REQUIRED_SCOPES")),
            service_documentation_url=os.environ.get("NOTEBOOKLM_MCP_SERVICE_DOCUMENTATION_URL"),
            access_token_ttl_seconds=_parse_int(
                "NOTEBOOKLM_MCP_ACCESS_TOKEN_TTL_SECONDS",
                DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
            ),
            refresh_token_ttl_seconds=_parse_int(
                "NOTEBOOKLM_MCP_REFRESH_TOKEN_TTL_SECONDS",
                DEFAULT_REFRESH_TOKEN_TTL_SECONDS,
            ),
            authorization_code_ttl_seconds=_parse_int(
                "NOTEBOOKLM_MCP_AUTHORIZATION_CODE_TTL_SECONDS",
                DEFAULT_AUTHORIZATION_CODE_TTL_SECONDS,
            ),
            client_secret_expiry_seconds=client_secret_expiry_seconds,
            tls_certfile=tls_certfile,
            tls_keyfile=tls_keyfile,
        )
