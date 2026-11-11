"""Person profiles, search, routing and place details."""

from __future__ import annotations

import orjson
from fastapi import APIRouter, HTTPException, Query

from ..db import fetch_all, fetch_one

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
    p = fetch_one(PERSON_SQL, {"id": person_id})
    if not p:
        raise HTTPException(404, "No such person")
    args = {"id": person_id, "hh": p["household_id"]}
    p["household"] = fetch_all(HOUSEHOLD_SQL, args)
    p["relatives"] = fetch_all(RELATIVES_SQL, args)
    p["schedule"] = fetch_all(SCHEDULE_SQL, args)
    return p


@router.get("/people/{person_id}/route")
def person_route(person_id: int):
    """GeoJSON FeatureCollection: one LineString per leg of the person's day."""
    legs = fetch_all("""
        SELECT a.seq, ST_X(g.geom) AS lon, ST_Y(g.geom) AS lat,
               (SELECT node_id FROM road_nodes n ORDER BY n.geom <-> g.geom LIMIT 1) AS node
        FROM activities a
        JOIN persons p    ON p.person_id = a.person_id
        JOIN households h ON h.household_id = p.household_id
        LEFT JOIN places pl ON pl.place_id = a.place_id
        CROSS JOIN LATERAL (SELECT COALESCE(pl.geom, h.geom) AS geom) g
        WHERE a.person_id = %(id)s
        ORDER BY a.seq
    """, {"id": person_id})
    if len(legs) < 2:
        return {"type": "FeatureCollection", "features": []}
    pairs = [(legs[i]["node"], legs[i + 1]["node"], legs[i]["seq"]) for i in range(len(legs) - 1)]
    values = ",".join(f"({int(a)},{int(b)})" for a, b, _ in pairs if a != b)
    if not values:
        return {"type": "FeatureCollection", "features": []}
    pad = 0.025  # ~2.5 km: route search is confined to the day's bounding box plus margin
    x0, x1 = min(l["lon"] for l in legs) - pad, max(l["lon"] for l in legs) + pad
    y0, y1 = min(l["lat"] for l in legs) - pad, max(l["lat"] for l in legs) + pad
    rows = fetch_all(f"""
        WITH r AS (
            SELECT * FROM pgr_dijkstra(
                $$SELECT edge_id AS id, source, target, cost_s AS cost, reverse_cost_s AS reverse_cost
                  FROM city.road_edges
                  WHERE geom && ST_MakeEnvelope({x0:.6f}, {y0:.6f}, {x1:.6f}, {y1:.6f}, 4326)$$,
                $$SELECT * FROM (VALUES {values}) v(source, target)$$,
                directed := true)
        )
        SELECT r.start_vid, r.end_vid,
               ST_AsGeoJSON(ST_LineMerge(ST_Collect(e.geom ORDER BY r.path_seq)), 6) AS geom,
               round(sum(e.length_m)) AS length_m, round(max(r.agg_cost) / 60) AS drive_min
        FROM r JOIN road_edges e ON e.edge_id = r.edge
        GROUP BY r.start_vid, r.end_vid
    """)
    by_pair = {(r["start_vid"], r["end_vid"]): r for r in rows}
    features = []
    for a, b, seq in pairs:
        r = by_pair.get((a, b))
        if r:
            features.append({"type": "Feature", "geometry": orjson.loads(r["geom"]),
                             "properties": {"leg": seq, "length_m": r["length_m"], "drive_min": r["drive_min"]}})
    return {"type": "FeatureCollection", "features": features}


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
