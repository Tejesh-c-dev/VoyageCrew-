"""Temporary diagnostic harness (delete after investigation).

Never prints connection strings, credentials, or raw exception text.
Inserts the 'psycopg.pool' logger attachment BEFORE creating the pool so
its startup diagnostics are captured too.
"""

import logging
import re
import sys
import time
import traceback

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tripmate.core.config import PROJECT_ROOT, load_settings
from tripmate.persistence.checkpointer import (
    CHECKPOINTER_POOL_TIMEOUT_SECONDS,
    create_checkpointer,
)

POOL_LOGGER = "psycopg.pool"
SENSITIVE = re.compile(
    r"(postgresql://|postgres://|password|passfile|sslkey|sslcert|"
    r"api[_-]?key|token|secret|credentials)",
    re.IGNORECASE,
)


class RedactingHandler(logging.Handler):
    """Log only sanitized event kinds: exception type names + SQLSTATEs."""

    def emit(self, record):
        # Whitelist the message vemplates we consider safe event kinds.
        msg = record.getMessage()
        kind = {
            "Appending connection to the pool": "pool.append",
            "Constructing new connection pool": "pool.construct",
            "Disposing connection pool": "pool.dispose",
            "Emitting notice": "pool.notice",
            "Connection failed": "pool.connection_failed",
            "Connection in idle state (%s s)": "pool.idle",
            "Closed connection to the database": "pool.closed_conn",
        }.get(msg)
        safe = {
            "level": record.levelname,
            "logger": record.name,
        }
        if kind is None:
            if SENSITIVE.search(msg) or len(msg) > 200:
                safe["event"] = "pool.other_redacted"
                self.format_and_emit(safe)
            return
        safe["kind"] = kind
        self.format_and_emit(safe)

    def format_and_emit(self, payload):
        print("[psycopg.pool]", payload, flush=True)


class SafeRootHandler(logging.Handler):
    """Fail-safe capture of unexpected exceptions: type name + SQLSTATE."""

    last_state = []

    def filter(self, record):
        return True

    def emit(self, record):
        # Skip tracer messages: our sanitized capture happens in probe().
        return


def describe_exception(exc) -> dict:
    """Type name + PostgreSQL SQLSTATE only. Never message/args."""
    info = {"exception_type": type(exc).__name__}
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate:
        info["sqlstate"] = sqlstate
    pgconn = None
    for attribute in ("pgconn", "diag"):
        pgconn = getattr(exc, attribute, None)
        if pgconn is not None:
            break
    try:
        state = getattr(pgconn, "pgresult", None) and getattr(
            pgconn.pgresult(), "xx", None
        )
    except Exception:
        state = None
    return info


def probe_readiness(resources, attempts=5, path="fresh"):
    """Run the same is_available() probe repeatedly; time each acquisition."""
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            ok = resources.is_available()
            elapsed = time.perf_counter() - started
            print(
                f"probe path={path} attempt={attempt} ok={ok} "
                f"elapsed_s={elapsed:.3f}",
                flush=True,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(
                f"probe path={path} attempt={attempt} FAILED "
                f"elapsed_s={elapsed:.3f} {describe_exception(exc)}",
                flush=True,
            )
            stats = getattr(resources.pool, "get_stats", None)
            if stats:
                print(
                    f"POOL STATS after failure: {resources.pool.get_stats()}",
                    flush=True,
                )
            traceback_str = "probe failed"
            raise


def measure_fresh_connects(runs=20):
    """Time raw psycopg connects incl. TLS to the configured endpoint."""
    settings = load_settings(PROJECT_ROOT)
    url = settings.require_database_url()
    timings = []
    for i in range(1, runs + 1):
        started = time.perf_counter()
        try:
            conn = psycopg.connect(
                url,
                connect_timeout=15,
                row_factory=dict_row,
            )
        except psycopg.OperationalError as exc:
            timings.append(None)
            print(f"connect run={i} FAILED {describe_exception(exc)}", flush=True)
            continue
        finally:
            elapsed = time.perf_counter() - started
        conn.execute("SELECT 1")
        conn.close()
        timings.append(elapsed)
        print(f"connect run={i} elapsed_s={elapsed:.3f}", flush=True)
        time.sleep(0.05)
    successful = [t for t in timings if t is not None]
    if successful:
        successful.sort()
        print(
            f"connect timings: n={len(successful)} "
            f"p50={successful[len(successful)//2]:.3f} "
            f"max={successful[-1]:.3f}",
            flush=True,
        )


def run_pool_cycle(cycles=12):
    """Replicate the integration-test lifecycle N times in ONE process."""
    for i in range(1, cycles + 1):
        started = time.perf_counter()
        try:
            settings = load_settings(PROJECT_ROOT)
            resources = create_checkpointer(settings)
        except Exception as exc:
            print(
                f"testimonial cycle={i} create FAILED elapsed_s="
                f"{time.perf_counter() - started:.3f} {describe_exception(exc)}",
                flush=True,
            )
            raise
        try:
            probe_readiness(resources, attempts=3, path=f"cycle{i}")
            stats = resources.pool.get_stats()
            print(f"cycle={i} stats={stats}", flush=True)
        finally:
            started_close = time.perf_counter()
            resources.close()
            print(
                f"cycle={i} closed elapsed_s="
                f"{time.perf_counter() - started_close:.3f} "
                f"duration={time.perf_counter() - started:.3f}",
                flush=True,
            )


if __name__ == "__main__":
    handler = RedactingHandler()
    pool_logger = logging.getLogger(POOL_LOGGER)
    pool_logger.addHandler(handler)
    pool_logger.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(SafeRootHandler())

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "connects"):
        print("=== fresh connect latency ===", flush=True)
        measure_fresh_connects(20)
    if mode in ("all", "cycles"):
        print("=== pool lifecycle cycles ===", flush=True)
        run_pool_cycle(12)
    if mode in ("all", "fastprobes"):
        print("=== probe under 0.25s timeout x N ===", flush=True)
        settings = load_settings(PROJECT_ROOT)
        resources = create_checkpointer(settings)
        try:
            for _ in range(25):
                resources.is_available()
                time.sleep(0.1)
                # every 10th probe, wait 35s to let the server-side
                # nothing waits; skip
        finally:
            resources.close()
    print("DONE", flush=True)
