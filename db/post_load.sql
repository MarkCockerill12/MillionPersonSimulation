-- Runs after bulk load: constraints, indexes, derived views, stats.
SET search_path = city, public;
SET maintenance_work_mem = '1GB';
SET max_parallel_maintenance_workers = 4;   -- build big indexes with parallel workers

-- ---------------------------------------------------------------- constraints
ALTER TABLE buildings  ADD FOREIGN KEY (district_id) REFERENCES districts;
ALTER TABLE places     ADD FOREIGN KEY (district_id) REFERENCES districts;
ALTER TABLE places     ADD FOREIGN KEY (building_id) REFERENCES buildings;
ALTER TABLE households ADD FOREIGN KEY (building_id) REFERENCES buildings;
ALTER TABLE households ADD FOREIGN KEY (district_id) REFERENCES districts;
ALTER TABLE persons    ADD FOREIGN KEY (household_id)  REFERENCES households;
ALTER TABLE persons    ADD FOREIGN KEY (occupation_id) REFERENCES occupations;
ALTER TABLE persons    ADD FOREIGN KEY (workplace_id)  REFERENCES places;
ALTER TABLE persons    ADD FOREIGN KEY (school_id)     REFERENCES places;
ALTER TABLE persons    ADD CONSTRAINT persons_citizen_id_key UNIQUE (citizen_id);
ALTER TABLE persons    ADD CONSTRAINT persons_email_key      UNIQUE (email);
ALTER TABLE persons    ADD CONSTRAINT persons_colour_key     UNIQUE (signature_colour);
ALTER TABLE persons    ADD CONSTRAINT persons_sex_chk CHECK (sex IN ('F', 'M'));
ALTER TABLE relationships ADD FOREIGN KEY (person_id)         REFERENCES persons;
ALTER TABLE relationships ADD FOREIGN KEY (related_person_id) REFERENCES persons;
ALTER TABLE relationships ADD CONSTRAINT rel_not_self CHECK (person_id <> related_person_id);
ALTER TABLE activities ADD FOREIGN KEY (person_id) REFERENCES persons;
ALTER TABLE activities ADD FOREIGN KEY (place_id)  REFERENCES places;
ALTER TABLE activities ADD CONSTRAINT act_time_chk CHECK (start_min <= end_min);
ALTER TABLE road_edges ADD FOREIGN KEY (source) REFERENCES road_nodes;
ALTER TABLE road_edges ADD FOREIGN KEY (target) REFERENCES road_nodes;

-- ---------------------------------------------------------------- indexes
CREATE INDEX ON districts  USING gist (geom);
CREATE INDEX ON buildings  USING gist (geom);
CREATE INDEX ON places     USING gist (geom);
CREATE INDEX ON places     (category);
CREATE INDEX ON households USING gist (geom);
CREATE INDEX ON households (building_id);
CREATE INDEX ON households (district_id);
CREATE INDEX ON road_nodes USING gist (geom);
CREATE INDEX ON road_edges (source);
CREATE INDEX ON road_edges (target);
CREATE INDEX ON persons (household_id);
CREATE INDEX ON persons (workplace_id);
CREATE INDEX ON persons (school_id);
CREATE INDEX ON persons (date_of_birth);
CREATE INDEX ON persons USING gin ((first_name || ' ' || last_name) gin_trgm_ops);
CREATE INDEX ON relationships (related_person_id);
CREATE INDEX ON activities (place_id);

-- ---------------------------------------------------------------- derived views

-- Every journey: consecutive activity pairs via LEAD().
CREATE MATERIALIZED VIEW mv_trips AS
WITH stops AS (
    SELECT a.person_id, a.seq, a.activity_type, a.start_min, a.end_min,
           COALESCE(pl.geom, h.geom)               AS geom,
           COALESCE(pl.district_id, h.district_id) AS district_id,
           p.commute_mode
    FROM activities a
    JOIN persons p      USING (person_id)
    JOIN households h   USING (household_id)
    LEFT JOIN places pl ON pl.place_id = a.place_id
), legs AS (
    SELECT person_id,
           seq                                  AS trip_seq,
           commute_mode                         AS mode,
           activity_type                        AS from_type,
           LEAD(activity_type) OVER w           AS to_type,
           district_id                          AS from_district,
           LEAD(district_id)   OVER w           AS to_district,
           end_min                              AS depart_min,
           LEAD(start_min)     OVER w           AS arrive_min,
           ST_DistanceSphere(geom, LEAD(geom) OVER w)::int AS straight_line_m
    FROM stops
    WINDOW w AS (PARTITION BY person_id ORDER BY seq)
)
SELECT * FROM legs WHERE to_type IS NOT NULL;
CREATE UNIQUE INDEX ON mv_trips (person_id, trip_seq);
CREATE INDEX ON mv_trips (from_district, to_district);

-- Headline indicators per district.
CREATE MATERIALIZED VIEW mv_district_stats AS
SELECT d.district_id,
       d.name,
       d.postcode_district,
       d.area_km2,
       count(p.person_id)                                                       AS population,
       count(DISTINCT p.household_id)                                           AS households,
       round(count(p.person_id) / NULLIF(d.area_km2, 0))::int                   AS density_per_km2,
       round(avg(date_part('year', age(DATE '2026-01-01', p.date_of_birth)))::numeric, 1) AS mean_age,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.annual_income)
           FILTER (WHERE p.annual_income > 0)                                   AS median_income,
       round(100.0 * count(*) FILTER (WHERE p.employment_status IN ('employed', 'self_employed'))
             / NULLIF(count(*) FILTER (WHERE date_part('year', age(DATE '2026-01-01', p.date_of_birth)) BETWEEN 16 AND 64), 0), 1)
                                                                                AS employment_rate_pct,
       round(100.0 * count(*) FILTER (WHERE p.commute_mode = 'car')
             / NULLIF(count(*) FILTER (WHERE p.commute_mode <> 'none'), 0), 1)  AS car_commute_pct
FROM districts d
LEFT JOIN households h ON h.district_id = d.district_id
LEFT JOIN persons p    ON p.household_id = h.household_id
GROUP BY d.district_id;
CREATE UNIQUE INDEX ON mv_district_stats (district_id);

-- Convenience view: person with age and home location.
CREATE VIEW v_person AS
SELECT p.*,
       date_part('year', age(DATE '2026-01-01', p.date_of_birth))::int AS age,
       h.district_id, h.geom AS home_geom, h.street, h.house_number, h.flat_number, h.postcode
FROM persons p
JOIN households h USING (household_id);

ANALYZE;

GRANT USAGE ON SCHEMA city TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA city TO analyst;
ALTER ROLE analyst SET search_path = city, public;
