"""Integration tests that require a disposable local PostgreSQL database."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.errors import UniqueViolation

from tripmate.api.auth import VerifiedIdentity
from tripmate.core.exceptions import ThreadNotFoundError
from tripmate.persistence.checkpointer import CheckpointerResources
from tripmate.persistence.ownership import ThreadOwnershipStore


class PostgresThreadOwnershipIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.getenv("TRIPMATE_TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("TRIPMATE_TEST_DATABASE_URL is not set")
        hostname = urlsplit(cls.database_url).hostname
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Ownership integration tests require local disposable PostgreSQL")
        cls.pool = ConnectionPool(
            cls.database_url,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
            min_size=1,
            max_size=8,
            open=True,
        )
        cls.resources = CheckpointerResources(cls.pool, saver=None)
        cls.resources.ensure_ownership_schema()
        cls.store = ThreadOwnershipStore(cls.pool)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "pool", None):
            cls.pool.close()

    def test_migration_and_identity_uniqueness_use_internal_uuid(self):
        identity = VerifiedIdentity("https://clerk.example.test", f"subject-{uuid4()}")
        owner_id = self.store.get_or_create_user(identity)
        self.assertEqual(owner_id.version, 4)

        duplicate_mapping_id = self.store.get_or_create_user(identity)
        self.assertEqual(owner_id, duplicate_mapping_id)

        with self.pool.connection() as connection, connection.cursor() as cursor:
            with self.assertRaises(UniqueViolation):
                cursor.execute(
                    "INSERT INTO tripmate_users (issuer, subject) VALUES (%s, %s)",
                    (identity.issuer, identity.subject),
                )

        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, issuer, subject FROM tripmate_users WHERE id = %s",
                (owner_id,),
            )
            row = cursor.fetchone()
        self.assertEqual(row["id"], owner_id)
        self.assertIsInstance(row["id"], type(owner_id))
        self.assertEqual((row["issuer"], row["subject"]), (identity.issuer, identity.subject))

    def test_concurrent_user_and_thread_creation_persist_ownership(self):
        identity = VerifiedIdentity("https://clerk.example.test", f"concurrent-{uuid4()}")
        with ThreadPoolExecutor(max_workers=8) as executor:
            owner_ids = list(executor.map(lambda _n: self.store.get_or_create_user(identity), range(16)))
        self.assertEqual(len(set(owner_ids)), 1)
        owner_id = owner_ids[0]

        thread_ids = [f"test-{uuid4()}" for _ in range(16)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda thread_id: self.store.create_thread(thread_id, owner_id), thread_ids))
        for thread_id in thread_ids:
            self.store.require_owner(thread_id, owner_id)

        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM tripmate_threads WHERE thread_id = ANY(%s) AND owner_id = %s",
                (thread_ids, owner_id),
            )
            self.assertEqual(cursor.fetchone()["count"], len(thread_ids))

        with self.assertRaises(ThreadNotFoundError):
            self.store.require_owner(thread_ids[0], uuid4())
        with self.assertRaises(ThreadNotFoundError):
            self.store.require_owner(f"absent-{uuid4()}", owner_id)

    def test_legacy_checkpoint_without_ownership_is_not_owned(self):
        saver = PostgresSaver(self.pool)
        saver.setup()
        thread_id = f"legacy-{uuid4()}"
        checkpoint_id = uuid4().hex
        config = saver.put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            {
                "v": 1,
                "id": checkpoint_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel_values": {},
                "channel_versions": {},
                "versions_seen": {},
            },
            {"source": "input", "step": -1, "writes": None, "parents": {}},
            {},
        )
        self.assertIsNotNone(saver.get_tuple(config))
        with self.assertRaises(ThreadNotFoundError):
            self.store.require_owner(thread_id, uuid4())


if __name__ == "__main__":
    unittest.main()
