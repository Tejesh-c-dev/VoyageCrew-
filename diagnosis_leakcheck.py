"""Pool lifecycle leak check (temporary): read-only session counts."""

import time

import psycopg
from tripmate.core.config import PROJECT_ROOT, load_settings
from tripmate.persistence.checkpointer import create_checkpointer

QUERY = (
    "SELECT count(*) FILTER (WHERE state <> 'idle') AS active, "
    "count(*) AS sessions FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
)

url = load_settings(PROJECT_ROOT).require_database_url()


def count(label):
    with psycopg.connect(url, connect_timeout=15) as conn:
        row = conn.execute(QUERY).fetchone()
        print(f"[activity] {label}: sessions={row[0]} active={row[1]}", flush=True)


pool = ConnectionPoolStub = None

print("=== leak + interference check ===", flush=True)
count("baseline (own spare conn excluded)")

for cycle in range(1, 4):
    resources = create_checkpointer(load_settings(PROJECT_ROOT))
    resources.is_available()
    # Simulate 'another component' requesting a connection while probe holds one,
    # then release both slowly (interference-ish ordering, still serialized).
    with resources.pool.connection(timeout=10) as connection:
        connection.execute("SELECT 1")
    stats = resources.pool.get_stats()
    print(f"cycle={cycle} stats={stats}", flush=True)
    resources.close()
    time.sleep(1.0)
    count(f"after cycle {cycle} + close")

count("final")
print("DONE", flush=True)
