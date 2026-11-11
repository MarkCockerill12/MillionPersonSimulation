"""Bulk-load DataFrames into Postgres with COPY (CSV over the wire, chunked)."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pandas as pd
import psycopg


def copy_df(conn: psycopg.Connection, table: str, df: pd.DataFrame, chunk: int = 200_000) -> None:
    t = time.perf_counter()
    cols = ", ".join(df.columns)
    with conn.cursor() as cur, cur.copy(f"COPY city.{table} ({cols}) FROM STDIN WITH (FORMAT csv)") as cp:
        for s in range(0, len(df), chunk):
            buf = io.StringIO()
            df.iloc[s:s + chunk].to_csv(buf, header=False, index=False)
            cp.write(buf.getvalue())
    conn.commit()
    print(f"  {table:<14} {len(df):>10,} rows  {time.perf_counter() - t:5.1f}s")


def run_sql(conn: psycopg.Connection, path: Path) -> None:
    t = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
    conn.commit()
    print(f"  {path.name} {time.perf_counter() - t:.1f}s")


def point_wkt(lon, lat) -> pd.Series:
    return "SRID=4326;POINT(" + pd.Series(lon).round(7).astype(str).values + " " + pd.Series(lat).round(7).astype(str).values + ")"
