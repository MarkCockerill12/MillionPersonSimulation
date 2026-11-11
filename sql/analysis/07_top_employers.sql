-- title: Largest employers
-- description: Workplaces by head-count, with average pay, staff commute distance (geography) and the most common mode.
-- chart: table
SELECT pl.name,
       pl.subcategory,
       d.name                                                                   AS district,
       count(*)                                                                 AS employees,
       round(avg(p.annual_income))                                              AS avg_income,
       round((avg(ST_Distance(h.geom::geography, pl.geom::geography)) / 1000)::numeric, 2) AS avg_commute_km,
       mode() WITHIN GROUP (ORDER BY p.commute_mode)                            AS typical_mode
FROM persons p
JOIN places pl    ON pl.place_id = p.workplace_id
JOIN households h ON h.household_id = p.household_id
JOIN districts d  ON d.district_id = pl.district_id
GROUP BY pl.place_id, pl.name, pl.subcategory, d.name
ORDER BY employees DESC
LIMIT 25;
