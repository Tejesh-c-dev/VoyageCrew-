"""PostgreSQL LangGraph checkpointer lifecycle."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tripmate.core.config import Settings
from tripmate.core.logging import annotate_database_error

CHECKPOINTER_PROBE_TIMEOUT_SECONDS = 0.25
CHECKPOINTER_POOL_MIN_SIZE = 1
CHECKPOINTER_POOL_MAX_SIZE = 5
CHECKPOINTER_POOL_TIMEOUT_SECONDS = 10
CHECKPOINTER_POOL_MAX_WAITING = 10


class DiagnosticPostgresSaver(PostgresSaver):
    """Temporarily label PostgreSQL errors raised while saving checkpoints."""

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        try:
            return super().put(config, checkpoint, metadata, new_versions)
        except psycopg.Error as exception:
            annotate_database_error(
                exception,
                operation="langgraph_checkpoint_put",
                phase="checkpoint_persistence",
            )
            raise

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        try:
            super().put_writes(config, writes, task_id, task_path)
        except psycopg.Error as exception:
            annotate_database_error(
                exception,
                operation="langgraph_checkpoint_put_writes",
                phase="checkpoint_persistence",
            )
            raise


@dataclass
class CheckpointerResources:
    pool: ConnectionPool[psycopg.Connection[dict[str, Any]]]
    saver: PostgresSaver

    def ensure_ownership_schema(self) -> None:
        migration = (
            Path(__file__).parent / "migrations" / "0001_thread_ownership.sql"
        ).read_text(encoding="utf-8")
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    for statement in migration.split(";"):
                        if statement.strip():
                            cursor.execute(statement)

    def is_available(self) -> bool:
        if self.pool.closed:
            return False

        with self.pool.connection(
            timeout=CHECKPOINTER_PROBE_TIMEOUT_SECONDS,
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '1000ms'")
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() is not None

    def close(self) -> None:
        self.pool.close()


def create_checkpointer(settings: Settings) -> CheckpointerResources:
    pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] = ConnectionPool(
        settings.require_database_url(),
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": None,
        },
        min_size=CHECKPOINTER_POOL_MIN_SIZE,
        max_size=CHECKPOINTER_POOL_MAX_SIZE,
        max_waiting=CHECKPOINTER_POOL_MAX_WAITING,
        timeout=CHECKPOINTER_POOL_TIMEOUT_SECONDS,
        open=False,
    )
    try:
        pool.open(
            wait=True,
            timeout=CHECKPOINTER_POOL_TIMEOUT_SECONDS,
        )
        saver = DiagnosticPostgresSaver(pool)
        saver.setup()
        resources = CheckpointerResources(pool, saver)
        resources.ensure_ownership_schema()
    except Exception:
        pool.close()
        raise

    return resources
