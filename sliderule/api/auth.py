"""Clerk JWT verification.

The org id and role come from the verified JWT — never from a request body or
query string. The verifier is injectable (app.state.token_verifier) so tests
never hit the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class AuthContext:
    clerk_user_id: str
    clerk_org_id: str
    role: str


class InvalidToken(Exception):
    pass


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AuthContext: ...


class ClerkVerifier:
    """Verifies Clerk session tokens against the instance JWKS.

    Lazy: the JWKS client (and the CLERK_JWKS_URL env read) happen on first
    verify, so importing or constructing this never touches the network.
    Handles both Clerk v1 session claims (org_id / org_role) and v2
    (o: {id, rol}).
    """

    def __init__(self, jwks_url: str | None = None):
        self._jwks_url = jwks_url
        self._jwk_client: jwt.PyJWKClient | None = None

    def _client(self) -> jwt.PyJWKClient:
        if self._jwk_client is None:
            url = self._jwks_url or os.environ.get("CLERK_JWKS_URL")
            if not url:
                raise RuntimeError("CLERK_JWKS_URL is not set")
            self._jwk_client = jwt.PyJWKClient(url, cache_keys=True)
        return self._jwk_client

    def verify(self, token: str) -> AuthContext:
        try:
            key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], options={"require": ["sub", "exp"]}
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc

        v2_org = claims.get("o") or {}
        org_id = claims.get("org_id") or v2_org.get("id")
        role = claims.get("org_role") or v2_org.get("rol") or ""
        if not org_id:
            raise InvalidToken("token has no active organization")
        return AuthContext(
            clerk_user_id=claims["sub"],
            clerk_org_id=org_id,
            role=role.removeprefix("org:"),
        )


_bearer = HTTPBearer(auto_error=False)


def get_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return verifier.verify(credentials.credentials)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
