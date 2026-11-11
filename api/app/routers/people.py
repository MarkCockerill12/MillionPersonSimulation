"""Person profiles, search, routing and place details."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from ..db import cursor, fetch_all, fetch_one
from ..routing import get_router

router = APIRouter(tags=["people"])

PERSON_SQL = """
SELECT p.person_id, p.citizen_id, p.first_name, p.middle_name, p.last_name, p.sex,
       p.date_of_birth, v.age, p.marital_status, p.email, p.phone, p.signature_colour,
       p.favourite_colour, p.eye_colour, p.hair_colour, p.height_cm, p.blood_type,
       p.education_level, p.employment_status, p.annual_income, p.commute_mode, p.household_role,
       o.title AS occupation, o.sector,
       w.place_id AS workplace_id, w.name AS workplace, w.category AS workplace_category,
       s.place_id AS school_id, s.name AS school,
       h.household_id, h.household_type, h.tenure, h.flat_number, h.house_number, h.street, h.postcode,
       d.name AS district, d.district_id,
       ST_X(h.geom) AS lon, ST_Y(h.geom) AS lat
FROM persons p
JOIN v_person v    USING (person_id)
JOIN households h  ON h.household_id = p.household_id
JOIN districts d   ON d.district_id = h.district_id
LEFT JOIN occupations o ON o.occupation_id = p.occupation_id
LEFT JOIN places w ON w.place_id = p.workplace_id
LEFT JOIN places s ON s.place_id = p.school_id
WHERE p.person_id = %(id)s
"""

HOUSEHOLD_SQL = """
SELECT p.person_id, p.first_name, p.last_name, p.signature_colour, p.household_role,
       date_part('year', age(DATE '2026-01-01', p.date_of_birth))::int AS age,
       r.relationship
FROM persons p
LEFT JOIN relationships r ON r.person_id = %(id)s AND r.related_person_id = p.person_id
WHERE p.household_id = %(hh)s AND p.person_id <> %(id)s
ORDER BY p.date_of_birth
"""

RELATIVES_SQL = """
SELECT r.relationship, p.person_id, p.first_name, p.last_name, p.signature_colour,
       date_part('year', age(DATE '2026-01-01', p.date_of_birth))::int AS age,
       h.street, h.house_number, h.postcode, d.name AS district,
       ST_X(h.geom) AS lon, ST_Y(h.geom) AS lat
FROM relationships r
JOIN persons p    ON p.person_id = r.related_person_id
JOIN households h ON h.household_id = p.household_id
JOIN districts d  ON d.district_id = h.district_id
WHERE r.person_id = %(id)s AND p.household_id <> %(hh)s
ORDER BY r.relationship, p.date_of_birth
"""

SCHEDULE_SQL = """
SELECT a.seq, a.activity_type, a.start_min, a.end_min, a.place_id,
       COALESCE(pl.name, 'Home') AS place_name, pl.category,
       ST_X(COALESCE(pl.geom, h.geom)) AS lon, ST_Y(COALESCE(pl.geom, h.geom)) AS lat
FROM activities a
JOIN persons p    ON p.person_id = a.person_id
JOIN households h ON h.household_id = p.household_id
LEFT JOIN places pl ON pl.place_id = a.place_id
WHERE a.person_id = %(id)s
ORDER BY a.seq
"""

@router.get("/people/{person_id}")
def person(person_id: int):
    """Full record: one pooled connection, four indexed lookups."""
    with cursor() as cur:
        cur.execute(PERSON_SQL, {"id": person_id})
        p = cur.fetchone()
        if not p:
            raise HTTPException(404, "No such person")
        args = {"id": person_id, "hh": p["household_id"]}
        for key, sql in (("household", HOUSEHOLD_SQL), ("relatives", RELATIVES_SQL), ("schedule", SCHEDULE_SQL)):
            cur.execute(sql, args)
            p[key] = cur.fetchall()
    return p


STOPS_SQL = """
SELECT a.seq, p.commute_mode, ST_X(COALESCE(pl.geom, h.geom)) AS lon, ST_Y(COALESCE(pl.geom, h.geom)) AS lat
FROM activities a
JOIN persons p    ON p.person_id = a.person_id
JOIN households h ON h.household_id = p.household_id
LEFT JOIN places pl ON pl.place_id = a.place_id
WHERE a.person_id = %(id)s
ORDER BY a.seq
"""


@router.get("/people/{person_id}/route")
def person_route(person_id: int):
    """GeoJSON FeatureCollection: one LineString per leg of the person's day (in-memory Dijkstra)."""
    t = time.perf_counter()
    stops = fetch_all(STOPS_SQL, {"id": person_id})
    r = get_router()
    profile = "active" if stops and stops[0]["commute_mode"] in ("walk", "cycle") else "car"
    nodes = [r.nearest(s["lon"], s["lat"]) for s in stops]
    features = []
    for i in range(len(stops) - 1):
        leg = r.route(nodes[i], nodes[i + 1], profile)
        if leg:
            coords, metres, _ = leg
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                             "properties": {"leg": stops[i]["seq"], "length_m": round(metres), "profile": profile}})
    return {"type": "FeatureCollection", "features": features,
            "routing_ms": round((time.perf_counter() - t) * 1000, 1)}


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80), limit: int = Query(20, le=50)):
    q = q.strip()
    if q.upper().startswith("BHM-"):
        return fetch_all("""
            SELECT person_id, citizen_id, first_name, last_name, signature_colour
            FROM persons WHERE citizen_id LIKE %(q)s ORDER BY citizen_id LIMIT %(n)s
        """, {"q": q.upper() + "%", "n": limit})
    if q.isdigit():
        return fetch_all("""SELECT person_id, citizen_id, first_name, last_name, signature_colour
                            FROM persons WHERE person_id = %(q)s""", {"q": int(q)})
    return fetch_all("""
        SELECT person_id, citizen_id, first_name, last_name, signature_colour,
               date_part('year', age(DATE '2026-01-01', date_of_birth))::int AS age
        FROM persons
        WHERE (first_name || ' ' || last_name) ILIKE %(q)s
        ORDER BY similarity(first_name || ' ' || last_name, %(raw)s) DESC
        LIMIT %(n)s
    """, {"q": f"%{q}%", "raw": q, "n": limit})


@router.get("/places/{place_id}")
def place(place_id: int):
    p = fetch_one("""
        SELECT pl.place_id, pl.name, pl.category, pl.subcategory, pl.capacity, d.name AS district,
               ST_X(pl.geom) AS lon, ST_Y(pl.geom) AS lat,
               (SELECT count(*) FROM persons WHERE workplace_id = pl.place_id) AS employees,
               (SELECT count(*) FROM persons WHERE school_id = pl.place_id)    AS students,
               (SELECT count(*) FROM activities WHERE place_id = pl.place_id)  AS daily_visits
        FROM places pl JOIN districts d USING (district_id)
        WHERE pl.place_id = %(id)s
    """, {"id": place_id})
    if not p:
        raise HTTPException(404, "No such place")
    p["sample_people"] = fetch_all("""
        SELECT p.person_id, p.first_name, p.last_name, p.signature_colour, a.activity_type, a.start_min, a.end_min
        FROM activities a JOIN persons p USING (person_id)
        WHERE a.place_id = %(id)s ORDER BY a.start_min LIMIT 25
    """, {"id": place_id})
    return p
