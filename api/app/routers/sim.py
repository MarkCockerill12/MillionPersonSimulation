"""Compact binary snapshot of every person's day, for the WebGL map.

One request ships the whole city (~40 MB raw, ~20 MB gzip) so the browser can
animate 1M dots locally without hitting the database every frame.

Layout (little-endian, n = number of people, index i = person_id - 1):
  0      char[4]  magic "MPS1"
  4      uint32   n
  8      f32[2n]  home lon/lat
         f32[2n]  stop-1 lon/lat (NaN if none)
         f32[2n]  stop-2 lon/lat (NaN if none)
         u16[6n]  home_end, s1_start, s1_end, s2_start, s2_end, home_start (minutes; 65535 = none)
         u16[n]   district_id
         u8[4n]   signature colour RGBA
         u8[n]    commute mode   (MODES index)
         u8[n]    employment status (STATUSES index)
         u8[n]    age
         u8[n]    stop-1 activity (ACTIVITIES index)
         u8[n]    stop-2 activity (ACTIVITIES index)
"""

from __future__ import annotations

import gzip
import io
import threading

import numpy as np
import pandas as pd
from fastapi import APIRouter, Response

from ..db import pool

router = APIRouter(prefix="/sim", tags=["simulation"])

MODES = ["none", "walk", "cycle", "bus", "train", "car"]
STATUSES = ["child", "pupil", "student", "employed", "self_employed", "unemployed", "carer", "retired"]
ACTIVITIES = ["home", "work", "school", "university", "shopping", "food_drink", "leisure", "healthcare"]

SNAPSHOT_SQL = """
COPY (
  SELECT p.person_id,
         ST_X(h.geom) AS hx, ST_Y(h.geom) AS hy,
         ST_X(p1.geom) AS s1x, ST_Y(p1.geom) AS s1y,
         ST_X(p2.geom) AS s2x, ST_Y(p2.geom) AS s2y,
         a0.end_min AS t0, a1.start_min AS t1, a1.end_min AS t2,
         a2.start_min AS t3, a2.end_min AS t4, al.start_min AS t5,
         h.district_id,
         p.signature_colour,
         p.commute_mode,
         p.employment_status,
         date_part('year', age(DATE '2026-01-01', p.date_of_birth))::int AS age,
         a1.activity_type AS act1,
         a2.activity_type AS act2
  FROM persons p
  JOIN households h ON h.household_id = p.household_id
  JOIN activities a0 ON a0.person_id = p.person_id AND a0.seq = 0
  LEFT JOIN activities a1 ON a1.person_id = p.person_id AND a1.seq = 1 AND a1.activity_type <> 'home'
  LEFT JOIN activities a2 ON a2.person_id = p.person_id AND a2.seq = 2 AND a2.activity_type <> 'home'
  LEFT JOIN LATERAL (SELECT start_min FROM activities x
                     WHERE x.person_id = p.person_id ORDER BY seq DESC LIMIT 1) al ON a1.person_id IS NOT NULL
  LEFT JOIN places p1 ON p1.place_id = a1.place_id
  LEFT JOIN places p2 ON p2.place_id = a2.place_id
  ORDER BY p.person_id
) TO STDOUT WITH (FORMAT csv, HEADER true)
"""

_lock = threading.Lock()
_cache: dict[str, bytes] = {}


def _codes(s: pd.Series, labels: list[str]) -> np.ndarray:
    return s.map({k: i for i, k in enumerate(labels)}).fillna(0).astype(np.uint8).to_numpy()


def build_snapshot() -> bytes:
    buf = io.BytesIO()
    with pool.connection() as conn, conn.cursor() as cur, cur.copy(SNAPSHOT_SQL) as cp:
        for chunk in cp:
            buf.write(chunk)
    buf.seek(0)
    df = pd.read_csv(buf)
    n = len(df)
    f = lambda cols: df[cols].to_numpy(dtype=np.float32).ravel()
    times = df[["t0", "t1", "t2", "t3", "t4", "t5"]].fillna(65535).to_numpy().astype(np.uint16).ravel()
    hexes = df["signature_colour"].str.slice(1).apply(lambda h: int(h, 16)).to_numpy(dtype=np.uint32)
    rgba = np.stack([(hexes >> 16) & 255, (hexes >> 8) & 255, hexes & 255, np.full(n, 255)], axis=1).astype(np.uint8)
    parts = [
        b"MPS1", np.uint32(n).tobytes(),
        f(["hx", "hy"]).tobytes(), f(["s1x", "s1y"]).tobytes(), f(["s2x", "s2y"]).tobytes(),
        times.tobytes(), df["district_id"].to_numpy(dtype=np.uint16).tobytes(),
        rgba.ravel().tobytes(),
        _codes(df["commute_mode"], MODES).tobytes(),
        _codes(df["employment_status"], STATUSES).tobytes(),
        df["age"].clip(0, 255).to_numpy(dtype=np.uint8).tobytes(),
        _codes(df["act1"], ACTIVITIES).tobytes(),
        _codes(df["act2"], ACTIVITIES).tobytes(),
    ]
    return gzip.compress(b"".join(parts), compresslevel=5)


def snapshot() -> bytes:
    with _lock:
        if "snap" not in _cache:
            _cache["snap"] = build_snapshot()
        return _cache["snap"]


@router.get("/meta")
def sim_meta():
    return {"modes": MODES, "statuses": STATUSES, "activities": ACTIVITIES}


@router.get("/snapshot.bin")
def sim_snapshot():
    return Response(snapshot(), media_type="application/octet-stream",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"})


@router.post("/snapshot/refresh")
def refresh():
    with _lock:
        _cache.pop("snap", None)
    return {"ok": True}
