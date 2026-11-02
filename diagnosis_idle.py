"""Reproduce idle-expiry PoolTimeout on the configured Neon endpoint (temporary).

- Keeps the application pool open across an idle gap (default 330s > Neon's
  ~5 min compute auto-suspend).
- Re-probes, capturing pool logger events, pool stats, and the underlying
  exception type + SQLSTATE (never credentials / conninfo).
- Verifies pool closure releases server connections via pg_stat_activity
  (read-only counts only).
"""

import logging
import time
import traceback

import psycopg
from psycopg.rows import dict_row

from tripmate.core.config import PROJECT_ROOT, load_settings
from tripmate.persistence.checkpointer import create_checkpointer

IDLE_SECONDS = float(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 330

captured = []


class RedactAndCapture(logging.Handler):
    def emit(self, record):
        safe = {
            "level": record.levelname,
            "logger": record.name,
            "msg": record.msg,  # template only; args (may contain conn bytes) excluded
        }
        captured.append(safe)
        print("[psycopg.pool]", safe, flush=True)


def redacted_exception(exc):
    out = {"exception_type": type(exc).__name__}
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate:
        out["sqlstate"] = sqlstate
    return out


def read_only_activity_count(label):
    """Count sessions on the current database via one spare connection."""
    settings = load_settings(PROJECT_ROOT)
    try:
        with psycopg.connect(
            settings.require_database_url(), connect_timeout=15, row_factory=dict_row
        ) as conn:
            row = conn.execute(
                "SELECT count(*) AS sessions, sum(state <> 'idle'::text) AS active "
                "FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid()"
            ).fetchone()
            print(
                f"[activity] {label} sessions={row['sessions']} active={row['active']}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[activity] {label} UNAVAILABLE {redacted_exception(exc)}", flush=True
        )


def main():
    logging.getLogger("psycopg.pool").addHandler(RedactAndCapture())
    logging.getLogger("psycopg.pool").setLevel(logging.DEBUG)

    print(f"=== idle-expiry reproduction, idle={IDLE_SECONDS}s ===", flush=True)
    read_only_activity_count("before")

    resources = create_checkpointer(load_settings(PROJECT_ROOT))
    t = time.perf_counter()
    try:
        ok = resources.is_available()
        print(f"pre-idle probe ok={ok} elapsed_s={time.perf_counter() - t:.3f}", flush=True)

        print(f"...sleeping {IDLE_SECONDS}s (idle gap)...", flush=True)
        time.sleep(IDLE_SECONDS)

        t = time.perf_counter()
        try:
            ok = resources.is_available()
            print(
                f"post-idle probe ok={ok} elapsed_s={time.perf_counter() - t:.3f}",
                flush=True,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t
            print(
                f"post-idle probe FAILED elapsed_s={elapsed:.3f} "
                f"{redacted_exception(exc)}",
                flush=True,
            )
            stats = resources.pool.get_stats()
            print(f"POOL STATS at failure: {stats}", flush=True)
            # Diagnostic only (NOT a production change): retry with a large
            # budget to see whether it recovers and how long recovery takes.
            t2 = time.perf_counter()
            try:
                ok2 = False
                with resources.pool.connection(timeout=60) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL statement_timeout = '1000ms'")
                        cur.execute("SELECT 1")
                        ok2 = cur.fetchone() is not None
                print(
                    f"post-idle probe retry(large budget) ok={ok2} "
                    f"elapsed_s={time.perf_counter() - t2:.3f} — issue was "
                    "acquisition budget, server is reachable",
                    flush=True,
                )
            except Exception as exc2:
                print(
                    f"post-idle probe retry also FAILED elapsed_s="
                    f"{time.perf_counter() - t2:.3f} {redacted_exception(exc2)} — "
                    "server-side/transport issue",
                    flush=True,
                )
            for line in traceback.format_exception_only(type(exc), exc):
                line = line.strip()
                if "postgresql://" not in line and "password" not in line:
                    print(f"  on-failing-acquire: {line}", flush=True)
    finally:
        try:
            resources.close()
            print("pool closed", flush=True)
        except Exception as exc:
            print(f"close FAILED {redacted_exception(exc)}", flush=True)

    read_only_activity_count("after-close")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
