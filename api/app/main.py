"""MillionProfile API."""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .db import analyst_pool, fetch_one, pool
from .routers import analytics, people, sim, sql_console
from .routing import get_router

_meta_cache: dict[str, dict] = {}


def _warm() -> None:
    """Build the in-memory road graph and cache the headline counts before the first visitor arrives."""
    try:
        t = time.perf_counter()
        router = get_router()
        meta()
        print(f"warm-up done in {time.perf_counter() - t:.2f}s (road graph {router.load_ms} ms)")
    except (psycopg.Error, ValueError, IndexError) as e:  # DB may be empty before the generator has run
        print(f"warm-up skipped: {e}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    analyst_pool.open()
    threading.Thread(target=_warm, daemon=True).start()
    yield
    pool.close()
    analyst_pool.close()


app = FastAPI(title="MillionProfile", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2048)

for r in (people.router, sim.router, analytics.router, sql_console.router):
    app.include_router(r, prefix="/api")


@app.get("/api/meta")
def meta():
    """Headline counts + city config. The data only changes when the generator runs, so cache it."""
    if "meta" not in _meta_cache:
        _meta_cache["meta"] = fetch_one("""
            SELECT (SELECT value FROM meta WHERE key = 'city') AS city,
                   (SELECT count(*) FROM persons)       AS people,
                   (SELECT count(*) FROM households)    AS households,
                   (SELECT count(*) FROM places)        AS places,
                   (SELECT count(*) FROM districts)     AS districts,
                   (SELECT count(*) FROM relationships) AS relationships,
                   (SELECT count(*) FROM activities)    AS activities,
                   (SELECT ARRAY[ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)]
                      FROM (SELECT ST_Extent(geom) e FROM districts) x) AS bbox,
                   pg_size_pretty(pg_database_size(current_database())) AS db_size
        """)
    return _meta_cache["meta"]
