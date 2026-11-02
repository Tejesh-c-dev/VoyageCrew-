"""Persistent mapping from verified identities to LangGraph threads."""

from typing import Any
from uuid import UUID

from psycopg_pool import ConnectionPool

from tripmate.api.auth import VerifiedIdentity
from tripmate.core.exceptions import ThreadNotFoundError


class ThreadOwnershipStore:
    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self._pool = pool

    def get_or_create_user(self, identity: VerifiedIdentity) -> UUID:
        with self._pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO tripmate_users (issuer, subject)
                           VALUES (%s, %s)
                           ON CONFLICT (issuer, subject) DO UPDATE
                           SET subject = EXCLUDED.subject
                           RETURNING id""",
                        (identity.issuer, identity.subject),
                    )
                    return cursor.fetchone()["id"]

    def create_thread(self, thread_id: str, owner_id: UUID) -> None:
        with self._pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO tripmate_threads (thread_id, owner_id) VALUES (%s, %s)",
                        (thread_id, owner_id),
                    )

    def require_owner(self, thread_id: str, owner_id: UUID) -> None:
        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM tripmate_threads WHERE thread_id = %s AND owner_id = %s",
                    (thread_id, owner_id),
                )
                if cursor.fetchone() is None:
                    raise ThreadNotFoundError()
