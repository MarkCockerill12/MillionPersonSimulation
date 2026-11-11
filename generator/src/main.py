"""MillionProfile generator: OSM -> synthetic population -> PostGIS (+ map snapshot, analytics cache).

  python -m src.main --city amsterdam --population 1000000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from faker import Faker
from psycopg.types.json import Jsonb

from . import cities, geo, people, schedule, snapshot
from .fetch_osm import fetch
from .load import copy_df, point_wkt, run_sql


class Timer:
    """Named wall-clock steps, printed as they finish and stored in city.meta."""

    def __init__(self) -> None:
        self.steps: dict[str, float] = {}
        self._name: str | None = None
        self._t = time.perf_counter()

    def step(self, name: str) -> None:
        self.stop()
        print(f"\n== {name}", flush=True)
        self._name, self._t = name, time.perf_counter()

    def stop(self) -> None:
        if self._name:
            self.steps[self._name] = round(time.perf_counter() - self._t, 2)
            print(f"   ({self.steps[self._name]:.1f}s)", flush=True)
            self._name = None


def _json_cell(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def precompute_analytics(conn: psycopg.Connection, sql_dir: Path) -> None:
    with conn.cursor() as cur:
        for path in sorted(sql_dir.glob("*.sql")):
            t = time.perf_counter()
            cur.execute(path.read_text(encoding="utf-8"))
            cols = [d.name for d in cur.description]
            rows = [[_json_cell(v) for v in r] for r in cur.fetchall()]
            ms = round((time.perf_counter() - t) * 1000)
            cur.execute("INSERT INTO city.analytics_results (analysis_id, columns, rows, elapsed_ms) VALUES (%s, %s, %s, %s)",
                        (path.stem, Jsonb(cols), Jsonb(rows), ms))
            print(f"  {path.stem:<32} {ms:>6} ms")
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default=os.environ.get("CITY", "amsterdam"))
    ap.add_argument("--population", type=int, default=int(os.environ.get("POPULATION", "1000000")))
    ap.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "/data"))
    ap.add_argument("--db-dir", default=os.environ.get("DB_DIR", "/db"))
    ap.add_argument("--sql-dir", default=os.environ.get("SQL_DIR", "/sql/analysis"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", "postgresql://city:city@localhost:5433/city"))
    args = ap.parse_args()
    t0 = time.perf_counter()
    city = cities.get(args.city)
    geo.configure(city.metric_crs, city.default_levels)
    people.configure(city)
    data = Path(args.data)
    osm = data / "osm" / city.key
    rng = np.random.default_rng(args.seed)
    Faker.seed(args.seed)
    faker = Faker("en_US")
    timer = Timer()
    print(f"City: {city.name}, {city.country}  |  population {args.population:,}  |  seed {args.seed}")

    if not (osm / "roads.graphml").exists():
        timer.step("fetch OSM")
        fetch(city, osm, data / "pbf" / city.pbf_url.rsplit("/", 1)[-1])

    timer.step("geography")
    g = geo.build(osm, rng, faker)
    n_d = len(g.districts)
    g.districts["postcode_district"] = [pc.split(" ")[0] for pc in city.postcode(
        np.arange(n_d), np.zeros(n_d, dtype=np.uint64), np.zeros(n_d, dtype=np.uint64), n_d)]

    timer.step(f"households & people (n={args.population:,})")
    households, persons, rel = people.compose_households(args.population, rng)
    rel_x = people.link_relatives_across_households(persons, rng)
    rel = pd.concat([rel, rel_x], ignore_index=True).drop_duplicates(["a", "b"])
    print(f"  {len(households):,} households, {len(rel):,} relationship rows")
    households = people.house_households(households, persons, g.buildings, g.street_of_building,
                                         g.house_number, g.districts, rng)
    persons = people.assign_identity(persons, rng)
    persons, occ = people.assign_work_and_school(persons, households, g.places, rng)
    persons["person_id"] = np.arange(1, len(persons) + 1, dtype=np.int32)

    spouse = set(rel.loc[rel.relationship == "spouse", "a"])
    partner = set(rel.loc[rel.relationship == "partner", "a"])
    idx = np.arange(len(persons))
    persons.loc[np.isin(idx, list(spouse)), "marital_status"] = "married"
    persons.loc[np.isin(idx, list(partner)), "marital_status"] = "cohabiting"

    timer.step("daily schedules")
    acts = schedule.build_activities(persons, g.places, households, rng)
    print(f"  {len(acts):,} activities; {persons.commute_mode.value_counts().to_dict()}")

    timer.step("map snapshot")
    hlon, hlat = geo.to_lonlat(households.x.to_numpy(), households.y.to_numpy())
    hh = persons["hh"].to_numpy()
    x0, y0, x1, y1 = g.boundary.bounds
    pad = 0.002
    bbox = (round(x0 - pad, 6), round(y0 - pad, 6), round(x1 + pad, 6), round(y1 + pad, 6))
    snapshot.write(data / "sim", persons, (hlon[hh], hlat[hh]), g.places, acts, bbox)

    timer.step("load into Postgres")
    with psycopg.connect(args.dsn) as conn:
        run_sql(conn, Path(args.db_dir) / "schema.sql")
        d = g.districts
        copy_df(conn, "districts", pd.DataFrame({
            "district_id": d.district_id, "name": d.name, "postcode_district": d.postcode_district,
            "area_km2": d.area_km2, "geom": "SRID=4326;" + d.geometry.to_wkt()}))
        b = g.buildings
        copy_df(conn, "buildings", pd.DataFrame({
            "building_id": b.building_id, "osm_id": b["id"], "district_id": b.district_id, "use_class": b.use_class,
            "levels": b.levels, "footprint_m2": b.footprint_m2, "geom": point_wkt(b.lon, b.lat)}))
        p = g.places
        copy_df(conn, "places", pd.DataFrame({
            "place_id": p.place_id, "building_id": pd.array(p.building_id, dtype="Int64"),
            "district_id": p.district_id, "category": p.category, "subcategory": p.subcategory,
            "name": p.name.str.slice(0, 120), "capacity": p.capacity, "geom": point_wkt(p.lon, p.lat)}))
        rn = g.road_nodes
        copy_df(conn, "road_nodes", pd.DataFrame({"node_id": rn.node_id, "geom": point_wkt(rn.lon, rn.lat)}))
        re_ = g.road_edges
        copy_df(conn, "road_edges", re_.drop(columns="wkt").assign(geom="SRID=4326;" + re_.wkt))
        occ_out = occ.assign(median_salary=(occ.median_salary * city.salary_factor).round(-2).astype(int))
        copy_df(conn, "occupations", occ_out[["occupation_id", "title", "sector", "median_salary"]])

        copy_df(conn, "households", pd.DataFrame({
            "household_id": np.arange(1, len(households) + 1), "building_id": households.building_id,
            "district_id": households.district_id, "household_type": households.type, "tenure": households.tenure,
            "flat_number": pd.array(np.where(households.flat_number > 0, households.flat_number, None), dtype="Int64"),
            "house_number": households.house_number, "street": households.street, "postcode": households.postcode,
            "geom": point_wkt(hlon, hlat)}))

        def nullable(s):
            return pd.array(np.where(s > 0, s, None), dtype="Int64")

        pp = persons
        copy_df(conn, "persons", pd.DataFrame({
            "person_id": pp.person_id, "citizen_id": pp.citizen_id, "household_id": pp.hh + 1,
            "household_role": pp.role, "first_name": pp.first_name, "middle_name": pp.middle_name,
            "last_name": pp.last_name, "sex": pp.sex, "date_of_birth": pp.date_of_birth,
            "marital_status": pp.marital_status, "email": pp.email, "phone": pp.phone,
            "signature_colour": pp.signature_colour, "favourite_colour": pp.favourite_colour,
            "eye_colour": pp.eye_colour, "hair_colour": pp.hair_colour, "height_cm": pp.height_cm,
            "blood_type": pp.blood_type, "education_level": pp.education_level,
            "employment_status": pp.employment_status, "occupation_id": nullable(pp.occupation_id),
            "workplace_id": nullable(pp.workplace_id), "school_id": nullable(pp.school_id),
            "annual_income": pp.annual_income, "commute_mode": pp.commute_mode}))
        copy_df(conn, "relationships", pd.DataFrame({
            "person_id": rel.a + 1, "related_person_id": rel.b + 1, "relationship": rel.relationship}))
        acts["place_id"] = nullable(acts.place_id)
        copy_df(conn, "activities", acts)

        timer.step("indexes, constraints, materialized views")
        run_sql(conn, Path(args.db_dir) / "post_load.sql")

        timer.step("precompute analytics")
        precompute_analytics(conn, Path(args.sql_dir))

        timer.stop()
        total = round(time.perf_counter() - t0, 1)
        city_meta = {
            "key": city.key, "name": city.name, "country": city.country, "currency": city.currency,
            "mode_labels": city.mode_labels, "id_prefix": city.id_prefix, "population": args.population,
            "seed": args.seed, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "generation_seconds": total, "steps": timer.steps,
        }
        with conn.cursor() as cur:
            cur.execute("INSERT INTO city.meta (key, value) VALUES ('city', %s)", (Jsonb(city_meta),))
        conn.commit()
        (data / "sim" / "city.json").write_text(json.dumps(city_meta), encoding="utf-8")

    print(f"\nDone in {total:.0f}s")


if __name__ == "__main__":
    main()
