"""MillionProfile API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse

from .db import analyst_pool, fetch_one, pool
from .routers import analytics, people, sim, sql_console


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    analyst_pool.open()
    yield
    pool.close()
    analyst_pool.close()


app = FastAPI(title="MillionProfile", default_response_class=ORJSONResponse, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2048)

for r in (people.router, sim.router, analytics.router, sql_console.router):
    app.include_router(r, prefix="/api")


@app.get("/api/meta")
def meta():
    return fetch_one("""
        SELECT (SELECT count(*) FROM persons)    AS people,
               (SELECT count(*) FROM households) AS households,
               (SELECT count(*) FROM places)     AS places,
               (SELECT count(*) FROM districts)  AS districts,
               (SELECT count(*) FROM relationships) AS relationships,
               (SELECT count(*) FROM activities) AS activities,
               (SELECT ARRAY[ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)]
                  FROM (SELECT ST_Extent(geom) e FROM districts) x) AS bbox,
               pg_size_pretty(pg_database_size(current_database())) AS db_size
    """)
