"""Read-only SQL console.

Runs as the `analyst` role: SELECT-only grants, read-only transactions and a
15 s statement timeout (see db/init/00_extensions.sql).
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import analyst_pool

router = APIRouter(prefix="/sql", tags=["sql"])
MAX_ROWS = 1000


class Query(BaseModel):
    sql: str = Field(max_length=20_000)


def _cell(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


@router.post("")
def run(q: Query):
    t = time.perf_counter()
    try:
        with analyst_pool.connection() as conn, conn.cursor() as cur:
            cur.execute(q.sql)
            if cur.description is None:
                return {"columns": [], "rows": [], "row_count": 0, "truncated": False,
                        "elapsed_ms": round((time.perf_counter() - t) * 1000)}
            cols = [d.name for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
    except psycopg.Error as e:
        raise HTTPException(400, str(e).strip()) from None
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"columns": cols, "rows": [[_cell(v) for v in r] for r in rows], "row_count": len(rows),
            "truncated": truncated, "elapsed_ms": round((time.perf_counter() - t) * 1000)}


@router.get("/schema")
def schema():
    with analyst_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE n.nspname = 'city' AND c.relkind IN ('r', 'v', 'm')
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relkind, c.relname, a.attnum
        """)
        out: dict[str, list] = {}
        for t, col, typ in cur.fetchall():
            out.setdefault(t, []).append({"name": col, "type": typ})
    return out
