-- title: School catchments
-- description: Pupils per school, mean home-to-school distance (geography, metres) and how they get there.
-- chart: table
SELECT s.name                                                                         AS school,
       d.name                                                                         AS district,
       count(*)                                                                       AS pupils,
       round(avg(ST_Distance(h.geom::geography, s.geom::geography))::numeric)         AS avg_distance_m,
       round(100.0 * count(*) FILTER (WHERE p.commute_mode = 'walk') / count(*), 1)   AS walk_pct,
       round(100.0 * count(*) FILTER (WHERE p.commute_mode = 'car')  / count(*), 1)   AS driven_pct
FROM persons p
JOIN places s     ON s.place_id = p.school_id AND s.category = 'school'
JOIN households h ON h.household_id = p.household_id
JOIN districts d  ON d.district_id = s.district_id
GROUP BY s.place_id, s.name, d.name
ORDER BY pupils DESC
LIMIT 30;
