"""Structured application logging and request correlation."""

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Generator
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "tripmate_correlation_id",
    default=None,
)
_STRUCTURED_FIELDS = (
    "agent",
    "attempt",
    "attempts",
    "duration_ms",
    "error_type",
    "error_code",
    "integration",
    "llm_calls",
    "method",
    "operation",
    "outcome",
    "path",
    "phase",
    "sqlstate",
    "message_primary",
    "table_name",
    "constraint_name",
    "finish_reason",
    "input_tokens",
    "output_tokens",
    "system_instruction_tokens",
    "current_request_tokens",
    "previous_history_tokens",
    "previous_user_request_count",
    "persisted_history_message_count",
    "included_history_tokens",
    "included_history_turns",
    "trip_constraint_tokens",
    "retrieved_evidence_tokens",
    "agent_output_tokens",
    "estimated_total_tokens",
    "initial_estimated_tokens",
    "input_budget_tokens",
    "total_reserve_tokens",
    "completion_reserve_tokens",
    "safety_reserve_tokens",
    "remaining_available_tokens",
    "compaction_level",
    "request_size_bytes",
    "configured_limit_bytes",
    "status_code",
    "thread_id_hash",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if any(getattr(handler, "_tripmate_structured", False) for handler in root_logger.handlers):
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredLogFormatter())
    setattr(handler, "_tripmate_structured", True)
    root_logger.addHandler(handler)


class StructuredLogFormatter(logging.Formatter):
    """Serialize only explicitly approved, non-sensitive log fields."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", "application.log")
        if not isinstance(event, str) or not all(
            character.isalnum() or character in "_.-"
            for character in event
        ) or not 1 <= len(event) <= 80:
            event = "application.log"

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                timezone.utc,
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
        }
        correlation_id = _CORRELATION_ID.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if field in {
                "sqlstate",
                "message_primary",
                "table_name",
                "constraint_name",
            }:
                if field in record.__dict__:
                    payload[field] = value if isinstance(value, str) else None
                continue
            if isinstance(value, (str, int, float)) and not isinstance(
                value,
                bool,
            ):
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def valid_correlation_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def set_correlation_id(value: str | None) -> Token[str | None]:
    return _CORRELATION_ID.set(valid_correlation_id(value))


def reset_correlation_id(token: Token[str | None]) -> None:
    _CORRELATION_ID.reset(token)


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


def annotate_database_error(
    exception: Exception,
    *,
    operation: str,
    phase: str,
) -> None:
    """Attach safe request-local phase labels for the database error handler."""
    if not hasattr(exception, "_tripmate_operation"):
        exception._tripmate_operation = operation
        exception._tripmate_phase = phase


@contextmanager
def correlation_context(value: str | None) -> Generator[None, None, None]:
    token = set_correlation_id(value or get_correlation_id())
    try:
        yield
    finally:
        reset_correlation_id(token)


def hash_thread_id(thread_id: str) -> str:
    return sha256(thread_id.encode("utf-8")).hexdigest()[:16]
