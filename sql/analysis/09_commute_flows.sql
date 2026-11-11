-- title: Top commuting corridors
-- description: Busiest district-to-district journeys to work (origin-destination matrix, top 25 off-diagonal pairs).
-- chart: table
SELECT o.name                                                              AS from_district,
       d.name                                                              AS to_district,
       count(*)                                                            AS commuters,
       round(avg(t.straight_line_m) / 1000.0, 2)                           AS avg_km,
       round(100.0 * count(*) FILTER (WHERE t.mode = 'car') / count(*), 1) AS car_pct
FROM mv_trips t
JOIN districts o ON o.district_id = t.from_district
JOIN districts d ON d.district_id = t.to_district
WHERE t.trip_seq = 0 AND t.to_type = 'work' AND t.from_district <> t.to_district
GROUP BY o.name, d.name
ORDER BY commuters DESC
LIMIT 25;
