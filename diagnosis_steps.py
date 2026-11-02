"""Fine-grained timing of the readiness probe steps (temporary)."""

import time

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tripmate.core.config import PROJECT_ROOT, load_settings
from tripmate.persistence.checkpointer import CHECKPOINTER_POOL_TIMEOUT_SECONDS

settings = load_settings(PROJECT_ROOT)
url = settings.require_database_url()

# Reproduce the exact pool kwargs from checkpointer.py
pool = ConnectionPool(
    url,
    kwargs={
        "autocommit": True,
        "row_factory": dict_row,
        "prepare_threshold": None,
    },
    min_size=1,
    max_size=5,
    max_waiting=10,
    timeout=CHECKPOINTER_POOL_TIMEOUT_SECONDS,
    open=False,
)

t0 = time.perf_counter()
pool.open(wait=False)
# Manually wait for fill like open(wait=True) would, to see fill duration
for _ in range(600):
    time.sleep(0.02)
    stats = pool.get_stats()
    if stats.get("connections_num", 0) >= 1 and stats.get("pool_available", 0) >= 1:
        break
print(f"pool fill elapsed_s={time.perf_counter() - t0:.3f}", flush=True)

try:
    for i in range(1, 6):
        t = time.perf_counter()
        with pool.connection(timeout=0.25) as connection:
            t_acquire = time.perf_counter()
            with connection.transaction() as txn:
                t_txn = time.perf_counter()
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '1000ms'")
                    t_set = time.perf_counter()
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
                    t_sel = time.perf_counter()
            t_commit = time.perf_counter()
        t_release = time.perf_counter()
        print(
            f"iter={i} acquire={t_acquire - t: .3f} txn={t_txn - t_acquire:.3f} "
            f"setlocal={t_set - t_txn:.3f} select01={t_sel - t_set:.3f} "
            f"commit={t_commit - t_sel:.3f} release={t_release - t_commit:.3f} "
            f"row={row}",
            flush=True,
        )
        if i == 2:
            # Compare: SELECT 1 WITHOUT the SET LOCAL, same connection
            t = time.perf_counter()
            with pool.connection(timeout=0.25) as connection:
                cursor = connection.cursor()
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
            print(f"no-setlocal select01 elapsed={time.perf_counter() - t:.3f} row={row}", flush=True)
finally:
    pool.close()
print("stats", pool.get_stats(), flush=True)
print("DONE", flush=True)
