"""Configuration for the remote NotebookLM MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8006
DEFAULT_ISSUER = "https://auth.jovanovic.org.uk"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _parse_port(name: str, default: int) -> int:
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


def _split_subs(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    subs = (sub.strip() for sub in value.split(","))
    return tuple(dict.fromkeys(sub for sub in subs if sub))


def _expand_path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _normalize_resource_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MCP_RESOURCE_URL must be an absolute http(s) URL")
    if parsed.scheme != "https" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("MCP_RESOURCE_URL must use https outside localhost")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("MCP_RESOURCE_URL cannot include params, query, or fragment")
    return value.rstrip("/")


@dataclass(frozen=True)
class RemoteServerConfig:
    """Environment-driven configuration for remote HTTP deployment.

    This process is an OAuth resource server only. ``issuer`` (Pocket ID) is
    the sole authorization server; ``resource_url`` is the audience every
    token must carry; ``allowed_subs`` is who may use the owner's NotebookLM
    login. An empty ``allowed_subs`` rejects every request.
    """

    host: str
    port: int
    resource_url: str
    issuer: str
    allowed_subs: tuple[str, ...]
    tls_certfile: Path | None
    tls_keyfile: Path | None

    @classmethod
    def from_env(cls) -> RemoteServerConfig:
        resource_url_raw = os.environ.get("MCP_RESOURCE_URL")
        if not resource_url_raw:
            raise ValueError("MCP_RESOURCE_URL is required for remote HTTP mode")

        tls_certfile = _expand_path(os.environ.get("NOTEBOOKLM_MCP_TLS_CERTFILE"))
        tls_keyfile = _expand_path(os.environ.get("NOTEBOOKLM_MCP_TLS_KEYFILE"))
        if (tls_certfile is None) != (tls_keyfile is None):
            raise ValueError(
                "NOTEBOOKLM_MCP_TLS_CERTFILE and NOTEBOOKLM_MCP_TLS_KEYFILE must be set together"
            )

        return cls(
            host=os.environ.get("NOTEBOOKLM_MCP_HOST", DEFAULT_HOST),
            port=_parse_port("NOTEBOOKLM_MCP_PORT", DEFAULT_PORT),
            resource_url=_normalize_resource_url(resource_url_raw),
            issuer=(os.environ.get("OAUTH_ISSUER") or DEFAULT_ISSUER).rstrip("/"),
            allowed_subs=_split_subs(os.environ.get("MCP_ALLOWED_SUBS")),
            tls_certfile=tls_certfile,
            tls_keyfile=tls_keyfile,
        )
