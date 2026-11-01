"""Request-scoped workflow and LLM invocation instrumentation."""

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from tripmate.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class LLMInvocationTracker:
    count: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )

    def record_invocation(self) -> None:
        with self._lock:
            self.count += 1

    def snapshot(self) -> int:
        with self._lock:
            return self.count


_LLM_TRACKER: ContextVar[LLMInvocationTracker | None] = ContextVar(
    "tripmate_llm_tracker",
    default=None,
)
_AGENT_NAME: ContextVar[str | None] = ContextVar(
    "tripmate_agent_name",
    default=None,
)


@contextmanager
def llm_invocation_context(
    tracker: LLMInvocationTracker | None,
) -> Generator[None, None, None]:
    token: Token[LLMInvocationTracker | None] = _LLM_TRACKER.set(tracker)
    try:
        yield
    finally:
        _LLM_TRACKER.reset(token)


@contextmanager
def agent_context(agent_name: str) -> Generator[None, None, None]:
    token: Token[str | None] = _AGENT_NAME.set(agent_name)
    try:
        yield
    finally:
        _AGENT_NAME.reset(token)


def invoke_llm(llm: Any, prompt: Any) -> Any:
    started_at = perf_counter()
    outcome = "success"
    try:
        return llm.invoke(prompt)
    except Exception:
        outcome = "failure"
        raise
    finally:
        tracker = _LLM_TRACKER.get()
        if tracker is not None:
            tracker.record_invocation()
        logger.log(
            logging.INFO if outcome == "success" else logging.ERROR,
            "LLM invocation completed.",
            extra={
                "event": "llm.invocation",
                "agent": _AGENT_NAME.get() or "workflow",
                "duration_ms": round(
                    (perf_counter() - started_at) * 1000,
                    2,
                ),
                "outcome": outcome,
            },
        )
