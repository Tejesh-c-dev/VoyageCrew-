"""Clerk bearer-token verification and stable user identity."""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError

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
        raise AuthenticationError("missing_authorization")

    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError("missing_authorization")

    try:
        jwks = _jwks_client(
            f"{settings.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
        )
        signing_key = jwks.get_signing_key_from_jwt(token).key
    except PyJWKClientConnectionError as error:
        raise AuthenticationError("jwks_retrieval") from error
    except Exception as error:
        # Key discovery can fail for transport, JWKS parsing, or unknown-kid
        # reasons. Keep the details server-side and distinct from JWT checks.
        raise AuthenticationError("jwks_key_lookup") from error

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.clerk_audience,
            issuer=settings.clerk_issuer.rstrip("/"),
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as error:
        raise AuthenticationError("expired_token") from error
    except jwt.ImmatureSignatureError as error:
        raise AuthenticationError("not_yet_valid_token") from error
    except jwt.InvalidIssuerError as error:
        raise AuthenticationError("issuer_mismatch") from error
    except jwt.InvalidAudienceError as error:
        raise AuthenticationError("audience_mismatch") from error
    except jwt.InvalidSignatureError as error:
        raise AuthenticationError("invalid_signature") from error
    except jwt.InvalidTokenError as error:
        raise AuthenticationError("invalid_token") from error
    except Exception as error:
        raise AuthenticationError("verification_error") from error

    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not subject or not isinstance(issuer, str):
        raise AuthenticationError("invalid_token")
    return VerifiedIdentity(issuer=issuer, subject=subject)
