"""Clerk bearer-token verification and stable user identity."""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from tripmate.core.config import Settings
from tripmate.core.exceptions import AuthenticationError, ConfigurationError


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str


@lru_cache(maxsize=16)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def verify_clerk_token(authorization: str | None, settings: Settings) -> VerifiedIdentity:
    if not settings.clerk_issuer or not settings.clerk_audience:
        raise ConfigurationError("Clerk authentication is not configured.")
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError()

    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError()

    try:
        jwks = _jwks_client(
            f"{settings.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
        )
        signing_key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.clerk_audience,
            issuer=settings.clerk_issuer.rstrip("/"),
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except Exception as error:
        # Provider/network/JWKS failures fail closed and reveal no token details.
        raise AuthenticationError() from error

    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not subject or not isinstance(issuer, str):
        raise AuthenticationError()
    return VerifiedIdentity(issuer=issuer, subject=subject)
