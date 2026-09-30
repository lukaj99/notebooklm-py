"""Resource-server token validation for the remote NotebookLM MCP server.

This process never issues tokens. Pocket ID is the only authorization server;
this module only checks the JWT access tokens it signed: RS256 via its
published JWKS, issuer, audience (RFC 8707: must name this resource), expiry,
the ``mcp:use`` scope, and finally that ``sub`` is on the allowlist.

The allowlist is what stops a second Pocket ID user, whose tokens are equally
well signed, from driving the owner's Google/NotebookLM login.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import jwt
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

REQUIRED_SCOPE = "mcp:use"

# Cloudflare's WAF 403s python-urllib's default User-Agent on this zone, which
# would make every token fail closed.
JWKS_USER_AGENT = "notebooklm-mcp-oauth/1.0"
JWKS_CACHE_SECONDS = 3600


class _JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def build_jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{issuer}/.well-known/jwks.json",
        cache_keys=True,
        lifespan=JWKS_CACHE_SECONDS,
        headers={"User-Agent": JWKS_USER_AGENT},
    )


def _scopes(claims: dict[str, Any]) -> list[str]:
    # Pocket ID emits the array form (scp); fall back to the RFC 8693
    # space-separated `scope` string.
    scp = claims.get("scp")
    if isinstance(scp, list):
        return [str(s) for s in scp]
    scope = claims.get("scope")
    return scope.split() if isinstance(scope, str) else []


class PocketIdTokenVerifier:
    """``mcp.server.auth.provider.TokenVerifier`` for Pocket ID access tokens."""

    def __init__(
        self,
        *,
        issuer: str,
        resource_url: str,
        allowed_subs: tuple[str, ...],
        jwks_client: _JwksClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._resource_url = resource_url
        # Blank entries must not become a match for an empty `sub`.
        self._allowed_subs = frozenset(sub for sub in allowed_subs if sub)
        self._jwks_client = jwks_client or build_jwks_client(issuer)
        if not self._allowed_subs:
            logger.error("MCP_ALLOWED_SUBS is empty: every request will be rejected")

    async def verify_token(self, token: str) -> AccessToken | None:
        # get_signing_key_from_jwt is synchronous and may fetch the JWKS, so
        # keep it off the event loop.
        return await asyncio.to_thread(self._verify, token)

    def _verify(self, token: str) -> AccessToken | None:
        if not self._allowed_subs:
            return None

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._resource_url,
                # PyJWT only checks these if present; without `require`, a
                # token lacking exp would never expire.
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            # Exception text never includes the token itself.
            logger.info("Bearer token rejected: %s", exc)
            return None

        scopes = _scopes(claims)
        if REQUIRED_SCOPE not in scopes:
            logger.info("Bearer token rejected: missing scope %r", REQUIRED_SCOPE)
            return None

        sub = str(claims["sub"])
        if sub not in self._allowed_subs:
            logger.info("Bearer token rejected: sub %r is not allowlisted", sub)
            return None

        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or sub),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self._resource_url,
        )
