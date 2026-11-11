"""MillionProfile generator: OSM -> synthetic population -> PostGIS.

  python -m src.main --population 1000000 --seed 42
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from faker import Faker

from . import geo, people, schedule
from .load import copy_df, point_wkt, run_sql


def step(name: str):
    print(f"\n== {name}", flush=True)
    return time.perf_counter()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", type=int, default=int(os.environ.get("POPULATION", "1000000")))
    ap.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    ap.add_argument("--osm", default=os.environ.get("OSM_DIR", "/data/osm"))
    ap.add_argument("--db-dir", default=os.environ.get("DB_DIR", "/db"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", "postgresql://city:city@localhost:5433/city"))
    args = ap.parse_args()
    t0 = time.perf_counter()
    osm = Path(args.osm)
    rng = np.random.default_rng(args.seed)
    Faker.seed(args.seed)
    faker = Faker("en_GB")

    if not (osm / "pois.parquet").exists():
        step("fetch OSM")
        subprocess.run([sys.executable, str(Path(__file__).with_name("fetch_osm.py")), "--out", str(osm)], check=True)

    step("geography")
    g = geo.build(osm, rng, faker)

    step(f"households & people (n={args.population:,})")
    households, persons, rel = people.compose_households(args.population, rng)
    rel_x = people.link_relatives_across_households(persons, rng)
    rel = pd.concat([rel, rel_x], ignore_index=True).drop_duplicates(["a", "b"])
    print(f"  {len(households):,} households, {len(rel):,} relationship rows")
    households = people.house_households(households, persons, g.buildings, g.street_of_building,
                                         g.house_number, g.districts, rng)
    persons = people.assign_identity(persons, rng)
    persons, occ = people.assign_work_and_school(persons, households, g.places, rng)
    persons["person_id"] = np.arange(1, len(persons) + 1, dtype=np.int32)

    # marital status from actual links
    spouse = set(rel.loc[rel.relationship == "spouse", "a"])
    partner = set(rel.loc[rel.relationship == "partner", "a"])
    idx = np.arange(len(persons))
    persons.loc[np.isin(idx, list(spouse)), "marital_status"] = "married"
    persons.loc[np.isin(idx, list(partner)), "marital_status"] = "cohabiting"

    step("daily schedules")
    acts = schedule.build_activities(persons, g.places, households, rng)
    print(f"  {len(acts):,} activities; {persons.commute_mode.value_counts().to_dict()}")

    step("load into Postgres")
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
        copy_df(conn, "occupations", occ[["occupation_id", "title", "sector", "median_salary"]])

        lon, lat = geo.to_lonlat(households.x.to_numpy(), households.y.to_numpy())
        copy_df(conn, "households", pd.DataFrame({
            "household_id": np.arange(1, len(households) + 1), "building_id": households.building_id,
            "district_id": households.district_id, "household_type": households.type, "tenure": households.tenure,
            "flat_number": pd.array(np.where(households.flat_number > 0, households.flat_number, None), dtype="Int64"),
            "house_number": households.house_number, "street": households.street, "postcode": households.postcode,
            "geom": point_wkt(lon, lat)}))

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

        step("indexes, constraints, materialized views")
        run_sql(conn, Path(args.db_dir) / "post_load.sql")

    print(f"\nDone in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
