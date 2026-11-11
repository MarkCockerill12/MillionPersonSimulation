"""Analytics endpoints backed by the documented queries in sql/analysis/*.sql.

Each file starts with `-- key: value` header lines (title, description, chart, x, y)
that tell the dashboard how to draw it. Results are cached because the data is static
between generator runs.
"""

from __future__ import annotations

import os
import threading
import time
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..db import cursor

router = APIRouter(prefix="/analytics", tags=["analytics"])
SQL_DIR = Path(os.environ.get("SQL_DIR", Path(__file__).resolve().parents[3] / "sql" / "analysis"))
_cache: dict[str, dict] = {}
_lock = threading.Lock()


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
def run_analysis(analysis_id: str):
    q = catalogue().get(analysis_id)
    if not q:
        raise HTTPException(404, "Unknown analysis")
    with _lock:
        if analysis_id in _cache:
            return _cache[analysis_id]
    t = time.perf_counter()
    with cursor(dict_rows=False) as cur:
        cur.execute(q["sql"])
        cols = [d.name for d in cur.description]
        rows = [[_clean(v) for v in r] for r in cur.fetchall()]
    result = {**q, "columns": cols, "rows": rows, "elapsed_ms": round((time.perf_counter() - t) * 1000)}
    with _lock:
        _cache[analysis_id] = result
    return result
