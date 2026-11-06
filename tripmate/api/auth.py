"""Database-backed password accounts and opaque browser sessions."""

import hashlib
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, VerifyMismatchError
from psycopg_pool import ConnectionPool

from tripmate.core.exceptions import AuthenticationError, BadRequestError

SESSION_COOKIE = "tripmate_session"
SESSION_LIFETIME = timedelta(days=14)
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(24))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountStore:
    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self.pool = pool

    def signup(self, email: str, password: str) -> tuple[UUID, str]:
        email = email.strip().lower()
        if len(email) > 254 or "@" not in email:
            raise BadRequestError("Enter a valid email address.")
        if len(password) < 10 or len(password) > 1024:
            raise BadRequestError("Password must be between 10 and 1024 characters.")
        hashed = PASSWORD_HASHER.hash(password)
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("INSERT INTO tripmate_users DEFAULT VALUES RETURNING id")
            row = cur.fetchone()
            user_id = row["id"]
            cur.execute("INSERT INTO tripmate_accounts (user_id, email, password_hash) VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING RETURNING user_id", (user_id, email, hashed))
            account = cur.fetchone()
            if account is None:
                raise BadRequestError("An account with that email already exists.")
            return user_id, self._new_session(cur, user_id)

    def login(self, email: str, password: str) -> tuple[UUID, str]:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT user_id AS id, password_hash FROM tripmate_accounts WHERE email = %s", (email.strip().lower(),))
            row = cur.fetchone()
            try:
                PASSWORD_HASHER.verify(row["password_hash"] if row is not None else DUMMY_PASSWORD_HASH, password)
            except (VerificationError, VerifyMismatchError):
                raise AuthenticationError("invalid_credentials") from None
            if row is None:
                raise AuthenticationError("invalid_credentials")
            return row["id"], self._new_session(cur, row["id"])

    @staticmethod
    def _new_session(cur: Any, user_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        cur.execute("INSERT INTO tripmate_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s)", (_token_hash(token), user_id, SESSION_LIFETIME))
        return token

    def user_for_session(self, token: str | None) -> UUID:
        if not token:
            raise AuthenticationError("missing_session")
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM tripmate_sessions WHERE token_hash = %s AND expires_at > now()", (_token_hash(token),))
            row = cur.fetchone()
        if row is None:
            raise AuthenticationError("invalid_session")
        return row["user_id"]

    def revoke(self, token: str | None) -> None:
        if token:
            with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute("DELETE FROM tripmate_sessions WHERE token_hash = %s", (_token_hash(token),))
