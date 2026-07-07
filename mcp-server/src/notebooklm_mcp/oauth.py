"""OAuth 2.1 provider for the remote NotebookLM MCP server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from pathlib import Path

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl, BaseModel, Field

from .config import RemoteServerConfig


def _hash_token(token: str) -> str:
    """One-way digest used to key/store bearer tokens at rest.

    Access and refresh tokens are only ever needed by value for a single
    dict lookup (the raw value arrives in the Authorization header / token
    request body and is hashed before comparison). Storing only the hash
    means a leaked copy of the state file cannot be replayed as a bearer
    token — see FileBackedOAuthProvider docstring.
    """

    return hashlib.sha256(token.encode()).hexdigest()


class PendingAuthorization(BaseModel):
    """Authorization request waiting for the local owner to approve it."""

    grant_id: str
    client_id: str
    redirect_uri: AnyUrl
    redirect_uri_provided_explicitly: bool
    state: str | None = None
    scopes: list[str] = Field(default_factory=list)
    code_challenge: str
    resource: str | None = None
    expires_at: float


class StoredAccessToken(AccessToken):
    """Access token with a pointer to its sibling refresh token."""

    refresh_token: str | None = None


class StoredRefreshToken(RefreshToken):
    """Refresh token with a pointer to its sibling access token."""

    access_token: str | None = None
    resource: str | None = None


class OAuthState(BaseModel):
    """File-backed state for dynamic clients and issued tokens.

    ``access_tokens`` and ``refresh_tokens`` are keyed by ``sha256(raw
    token)``, and the ``token``/``access_token``/``refresh_token`` fields
    stored inside each entry are likewise the hash, never the raw secret.
    The raw value is only ever handed to the client once, at issuance time
    (see ``OAuthToken`` return values below) — it is never written to disk.
    """

    clients: dict[str, OAuthClientInformationFull] = Field(default_factory=dict)
    pending_authorizations: dict[str, PendingAuthorization] = Field(default_factory=dict)
    authorization_codes: dict[str, AuthorizationCode] = Field(default_factory=dict)
    refresh_tokens: dict[str, StoredRefreshToken] = Field(default_factory=dict)
    access_tokens: dict[str, StoredAccessToken] = Field(default_factory=dict)
    trusted_client_ids: set[str] = Field(default_factory=set)


class FileBackedOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, StoredRefreshToken, StoredAccessToken]
):
    """Minimal OAuth 2.1 AS/RS provider suitable for a single-user MCP server.

    ``OAUTH_AUTO_APPROVE`` skips the interactive consent screen so a client
    that has *already* completed one owner-approved authorization (password
    or trusted Cloudflare Access identity — see ``trust_client``) can
    reconnect without a manual click every time. It intentionally does NOT
    let a brand-new, never-approved client skip consent: dynamic client
    registration (``register_client``) has no credential of its own, so
    auto-approving on first contact would let anyone who can reach
    ``/register`` + ``/authorize`` mint a token with zero authentication.
    """

    def __init__(self, config: RemoteServerConfig):
        self.config = config
        self._lock = asyncio.Lock()
        self._path = config.oauth_store_path
        self._state = self._load_state()
        self.auto_approve = os.getenv("OAUTH_AUTO_APPROVE", "").lower() in ("true", "1")
        if self.auto_approve:
            import logging

            logging.getLogger(__name__).info(
                "OAuth auto-approve enabled (OAUTH_AUTO_APPROVE=true) for "
                "previously-trusted clients only"
            )

    def _load_state(self) -> OAuthState:
        if not self._path.exists():
            return OAuthState()
        return OAuthState.model_validate_json(self._path.read_text())

    def _save_state(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = self._state.model_dump_json(indent=2)
        tmp_path = Path(f"{self._path}.tmp")
        tmp_path.write_text(payload)
        tmp_path.chmod(0o600)
        tmp_path.replace(self._path)
        self._path.chmod(0o600)

    def _prune_expired(self) -> None:
        now = time.time()
        self._state.pending_authorizations = {
            key: value
            for key, value in self._state.pending_authorizations.items()
            if value.expires_at > now
        }
        self._state.authorization_codes = {
            key: value
            for key, value in self._state.authorization_codes.items()
            if value.expires_at > now
        }
        self._state.refresh_tokens = {
            key: value
            for key, value in self._state.refresh_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }
        self._state.access_tokens = {
            key: value
            for key, value in self._state.access_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._lock:
            self._prune_expired()
            return self._state.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required")

        async with self._lock:
            self._prune_expired()
            self._state.clients[client_info.client_id] = client_info
            self._save_state()

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        grant_id = secrets.token_urlsafe(32)
        scopes = params.scopes or list(self.config.required_scopes)
        pending = PendingAuthorization(
            grant_id=grant_id,
            client_id=client.client_id or "",
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            state=params.state,
            scopes=scopes,
            code_challenge=params.code_challenge,
            resource=params.resource,
            expires_at=time.time() + self.config.authorization_code_ttl_seconds,
        )

        async with self._lock:
            self._prune_expired()
            self._state.pending_authorizations[grant_id] = pending
            is_trusted_client = pending.client_id in self._state.trusted_client_ids
            self._save_state()

        # Auto-approve only re-authorizes a client that has already been
        # through owner-gated consent once before (see trust_client). A
        # never-seen client_id always falls through to the consent page,
        # regardless of OAUTH_AUTO_APPROVE — otherwise anyone able to hit
        # the (intentionally open) /register + /authorize endpoints could
        # mint a token with zero credentials.
        if self.auto_approve and is_trusted_client:
            redirect_url = await self.approve_pending_authorization(grant_id)
            if redirect_url:
                return redirect_url

        return f"{self.config.issuer_url}/oauth/consent?grant_id={grant_id}"

    async def trust_client(self, client_id: str) -> None:
        """Remember that a client has completed owner-gated consent once.

        Called after a successful password (or trusted Cloudflare Access
        identity) approval in the /oauth/consent route. Only clients
        recorded here are eligible for OAUTH_AUTO_APPROVE on subsequent
        reconnects.
        """

        async with self._lock:
            self._prune_expired()
            self._state.trusted_client_ids.add(client_id)
            self._save_state()

    async def get_pending_authorization(self, grant_id: str) -> PendingAuthorization | None:
        async with self._lock:
            self._prune_expired()
            return self._state.pending_authorizations.get(grant_id)

    async def approve_pending_authorization(self, grant_id: str) -> str | None:
        async with self._lock:
            self._prune_expired()
            pending = self._state.pending_authorizations.pop(grant_id, None)
            if pending is None:
                return None

            code = secrets.token_urlsafe(48)
            authorization_code = AuthorizationCode(
                code=code,
                scopes=pending.scopes,
                expires_at=time.time() + self.config.authorization_code_ttl_seconds,
                client_id=pending.client_id,
                code_challenge=pending.code_challenge,
                redirect_uri=pending.redirect_uri,
                redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
                resource=pending.resource,
            )
            self._state.authorization_codes[code] = authorization_code
            self._save_state()

        return construct_redirect_uri(
            str(pending.redirect_uri),
            code=code,
            state=pending.state,
        )

    async def deny_pending_authorization(self, grant_id: str) -> str | None:
        async with self._lock:
            self._prune_expired()
            pending = self._state.pending_authorizations.pop(grant_id, None)
            if pending is None:
                return None
            self._save_state()

        return construct_redirect_uri(
            str(pending.redirect_uri),
            error="access_denied",
            error_description="Access denied by NotebookLM MCP owner",
            state=pending.state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        async with self._lock:
            self._prune_expired()
            code = self._state.authorization_codes.get(authorization_code)
            if code is None or code.client_id != client.client_id:
                return None
            return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        async with self._lock:
            self._prune_expired()
            self._state.authorization_codes.pop(authorization_code.code, None)

            access_value = secrets.token_urlsafe(48)
            refresh_value = secrets.token_urlsafe(48)
            access_hash = _hash_token(access_value)
            refresh_hash = _hash_token(refresh_value)
            access_expires_at = int(time.time()) + self.config.access_token_ttl_seconds
            refresh_expires_at = int(time.time()) + self.config.refresh_token_ttl_seconds

            access_token = StoredAccessToken(
                token=access_hash,
                client_id=client.client_id or "",
                scopes=authorization_code.scopes,
                expires_at=access_expires_at,
                resource=authorization_code.resource,
                refresh_token=refresh_hash,
            )
            refresh_token = StoredRefreshToken(
                token=refresh_hash,
                client_id=client.client_id or "",
                scopes=authorization_code.scopes,
                expires_at=refresh_expires_at,
                access_token=access_hash,
                resource=authorization_code.resource,
            )

            self._state.access_tokens[access_hash] = access_token
            self._state.refresh_tokens[refresh_hash] = refresh_token
            self._save_state()

        return OAuthToken(
            access_token=access_value,
            expires_in=self.config.access_token_ttl_seconds,
            refresh_token=refresh_value,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> StoredRefreshToken | None:
        async with self._lock:
            self._prune_expired()
            token = self._state.refresh_tokens.get(_hash_token(refresh_token))
            if token is None or token.client_id != client.client_id:
                return None
            return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: StoredRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with self._lock:
            self._prune_expired()
            # refresh_token.token / .access_token are already hashes (this
            # object came from load_refresh_token), so these pops line up
            # with the hash-keyed dicts below.
            self._state.refresh_tokens.pop(refresh_token.token, None)
            if refresh_token.access_token:
                self._state.access_tokens.pop(refresh_token.access_token, None)

            access_value = secrets.token_urlsafe(48)
            refresh_value = secrets.token_urlsafe(48)
            access_hash = _hash_token(access_value)
            refresh_hash = _hash_token(refresh_value)
            access_expires_at = int(time.time()) + self.config.access_token_ttl_seconds
            refresh_expires_at = int(time.time()) + self.config.refresh_token_ttl_seconds

            new_access_token = StoredAccessToken(
                token=access_hash,
                client_id=client.client_id or "",
                scopes=scopes,
                expires_at=access_expires_at,
                resource=refresh_token.resource,
                refresh_token=refresh_hash,
            )
            new_refresh_token = StoredRefreshToken(
                token=refresh_hash,
                client_id=client.client_id or "",
                scopes=scopes,
                expires_at=refresh_expires_at,
                access_token=access_hash,
                resource=refresh_token.resource,
            )

            self._state.access_tokens[access_hash] = new_access_token
            self._state.refresh_tokens[refresh_hash] = new_refresh_token
            self._save_state()

        return OAuthToken(
            access_token=access_value,
            expires_in=self.config.access_token_ttl_seconds,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        async with self._lock:
            self._prune_expired()
            return self._state.access_tokens.get(_hash_token(token))

    async def revoke_token(self, token: StoredAccessToken | StoredRefreshToken) -> None:
        async with self._lock:
            self._prune_expired()

            access_token_value: str | None = None
            refresh_token_value: str | None = None

            if isinstance(token, StoredAccessToken):
                access_token_value = token.token
                refresh_token_value = token.refresh_token
            elif isinstance(token, StoredRefreshToken):
                refresh_token_value = token.token
                access_token_value = token.access_token

            if access_token_value:
                self._state.access_tokens.pop(access_token_value, None)
            if refresh_token_value:
                self._state.refresh_tokens.pop(refresh_token_value, None)
            self._save_state()
