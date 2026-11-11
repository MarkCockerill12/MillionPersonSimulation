"""Analytics endpoints backed by the documented queries in sql/analysis/*.sql.

Each file starts with `-- key: value` header lines (title, description, chart, x, y)
that tell the dashboard how to draw it.

Results are precomputed by the generator into city.analytics_results, so the dashboard
reads twelve small JSON rows instead of scanning millions of records. `?live=1` re-runs
the query against the live tables and reports how long it took.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..db import cursor, fetch_one

router = APIRouter(prefix="/analytics", tags=["analytics"])
SQL_DIR = Path(os.environ.get("SQL_DIR", Path(__file__).resolve().parents[3] / "sql" / "analysis"))


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("--"):
            break
        key, _, val = line[2:].strip().partition(":")
        meta[key.strip()] = val.strip()
    return {"id": path.stem, "sql": text, **meta}


def catalogue() -> dict[str, dict]:
    return {p.stem: _parse(p) for p in sorted(SQL_DIR.glob("*.sql"))}


def _clean(v):
    return float(v) if isinstance(v, Decimal) else v


@router.get("")
def list_analyses():
    return [{k: v for k, v in q.items() if k != "sql"} for q in catalogue().values()]


@router.get("/{analysis_id}")
def run_analysis(analysis_id: str, live: bool = Query(False)):
    q = catalogue().get(analysis_id)
    if not q:
        raise HTTPException(404, "Unknown analysis")
    if not live:
        t = time.perf_counter()
        hit = fetch_one("""SELECT columns, rows, elapsed_ms, computed_at FROM analytics_results
                           WHERE analysis_id = %(id)s""", {"id": analysis_id})
        if hit:
            return {**q, "columns": hit["columns"], "rows": hit["rows"], "elapsed_ms": hit["elapsed_ms"],
                    "served_ms": round((time.perf_counter() - t) * 1000, 1), "source": "precomputed",
                    "computed_at": hit["computed_at"]}
    t = time.perf_counter()
    with cursor(dict_rows=False) as cur:
        cur.execute(q["sql"])
        cols = [d.name for d in cur.description]
        rows = [[_clean(v) for v in r] for r in cur.fetchall()]
    ms = round((time.perf_counter() - t) * 1000)
    return {**q, "columns": cols, "rows": rows, "elapsed_ms": ms, "served_ms": ms, "source": "live"}
