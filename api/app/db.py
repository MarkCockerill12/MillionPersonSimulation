"""Connection pools: `pool` (app queries) and `analyst_pool` (read-only SQL console)."""

from __future__ import annotations

import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get("DATABASE_URL", "postgresql://city:city@localhost:5433/city")
ANALYST_DSN = os.environ.get("ANALYST_DATABASE_URL", "postgresql://analyst:analyst@localhost:5433/city")

pool = ConnectionPool(DSN, min_size=1, max_size=10, open=False,
                      kwargs={"options": "-c search_path=city,public -c statement_timeout=30000"})


def _reset(conn) -> None:
    # Undo any SET a console user ran, so role defaults (read-only, timeout) apply to the next user.
    conn.execute("RESET ALL")


analyst_pool = ConnectionPool(ANALYST_DSN, min_size=0, max_size=4, open=False, reset=_reset,
                              kwargs={"options": "-c search_path=city,public", "autocommit": True})


@contextmanager
def cursor(dict_rows: bool = True):
    with pool.connection() as conn, conn.cursor(row_factory=dict_row if dict_rows else None) as cur:
        yield cur


def fetch_all(sql: str, params: dict | tuple | None = None) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: dict | tuple | None = None) -> dict | None:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()
