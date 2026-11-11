"""Write the map snapshot: everyone's day as compact binary chunks for the GPU layer.

Columnar, quantised, 32 bytes per person:
  coordinates  unorm16 within the city bounding box (about 0.3 m precision for a 20 km city)
  times        uint16 minutes (65535 = no such stop)
  colour       RGBA bytes; meta bytes = travel mode, age, stop-1 activity, stop-2 activity

The chunks are pre-gzipped so nginx can serve them as static files with no API involvement,
and the browser can fetch them in parallel and draw each one as soon as it arrives.
Layout is mirrored in web/src/sim.ts.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd

NONE = 65535
MODES = ["none", "walk", "cycle", "bus", "train", "car"]
ACTIVITIES = ["home", "work", "school", "university", "shopping", "food_drink", "leisure", "healthcare"]


def _codes(values: np.ndarray, labels: list[str]) -> np.ndarray:
    codes = pd.Categorical(values, categories=labels).codes
    return np.where(codes < 0, 0, codes).astype(np.uint8)


def write(out_dir: Path, persons: pd.DataFrame, home_lonlat: tuple[np.ndarray, np.ndarray],
          places: pd.DataFrame, acts: pd.DataFrame, bbox: tuple[float, float, float, float],
          chunk_size: int = 125_000) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("chunk-*"):
        old.unlink()
    n = len(persons)
    x0, y0, x1, y1 = bbox

    def q(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        qx = np.clip(np.round((lon - x0) / (x1 - x0) * 65535), 0, 65535)
        qy = np.clip(np.round((lat - y0) / (y1 - y0) * 65535), 0, 65535)
        return np.stack([qx, qy], axis=1).astype(np.uint16)

    hlon, hlat = home_lonlat
    home = q(hlon, hlat)

    # stops 1 and 2 (non-home) per person, via the activities table
    pl = places.set_index("place_id")[["lon", "lat"]]
    pid = persons["person_id"].to_numpy()
    idx = pd.Series(np.arange(n), index=pid)

    def stop(seq: int):
        a = acts[(acts.seq == seq) & (acts.activity_type != "home")]
        rows = idx.loc[a.person_id].to_numpy()
        coords = home.copy()
        coords[rows] = q(pl.loc[a.place_id, "lon"].to_numpy(), pl.loc[a.place_id, "lat"].to_numpy())
        act = np.zeros(n, dtype=np.uint8)
        act[rows] = _codes(a.activity_type.to_numpy(), ACTIVITIES)
        start = np.full(n, NONE, dtype=np.uint16); end = np.full(n, NONE, dtype=np.uint16)
        start[rows] = a.start_min.to_numpy(); end[rows] = a.end_min.to_numpy()
        return coords, act, start, end

    s1, act1, s1_start, s1_end = stop(1)
    s2, act2, s2_start, s2_end = stop(2)
    first = acts[acts.seq == 0]
    home_end = np.full(n, NONE, dtype=np.uint16)
    home_end[idx.loc[first.person_id].to_numpy()] = first.end_min.to_numpy()
    last = acts.groupby("person_id", sort=False).tail(1)
    home_start = np.full(n, NONE, dtype=np.uint16)
    home_start[idx.loc[last.person_id].to_numpy()] = last.start_min.to_numpy()
    times_a = np.stack([home_end, s1_start, s1_end, s2_start], axis=1)
    times_b = np.stack([s2_end, home_start], axis=1)

    rgb = np.frombuffer(bytes.fromhex("".join(persons["signature_colour"].str.slice(1))), dtype=np.uint8).reshape(n, 3)
    colour = np.concatenate([rgb, np.full((n, 1), 255, dtype=np.uint8)], axis=1)
    meta = np.stack([_codes(persons["commute_mode"].to_numpy(), MODES),
                     persons["age"].clip(0, 255).to_numpy().astype(np.uint8), act1, act2], axis=1)

    chunks = []
    for k, start in enumerate(range(0, n, chunk_size)):
        sl = slice(start, min(n, start + chunk_size))
        c = sl.stop - sl.start
        body = b"".join([
            b"MPS2", struct.pack("<III", start, c, 0),
            home[sl].tobytes(), s1[sl].tobytes(), s2[sl].tobytes(),
            times_a[sl].tobytes(), times_b[sl].tobytes(), colour[sl].tobytes(), meta[sl].tobytes(),
        ])
        name = f"chunk-{k:03d}.bin"
        (out_dir / name).write_bytes(body)
        gz = gzip.compress(body, compresslevel=6)
        (out_dir / f"{name}.gz").write_bytes(gz)
        version = hashlib.sha1(body).hexdigest()[:12]  # cache-buster: files are served as immutable
        chunks.append({"url": f"/api/sim/{name}?v={version}", "start": start, "count": c, "bytes": len(gz), "raw_bytes": len(body)})

    manifest = {"version": 2, "n": n, "bbox": [x0, y0, x1, y1], "modes": MODES, "activities": ACTIVITIES,
                "chunks": chunks}
    (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    total = sum(c["bytes"] for c in chunks)
    print(f"  {len(chunks)} chunks, {sum(c['raw_bytes'] for c in chunks) / 1e6:.1f} MB raw, {total / 1e6:.1f} MB gzipped")
    return manifest
