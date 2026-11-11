-- title: Household composition
-- description: How people live together: households, residents and mean size by household type.
-- chart: bar
-- x: household_type
-- y: households
WITH sizes AS (
    SELECT h.household_type, h.household_id, count(p.person_id) AS members
    FROM households h
    JOIN persons p USING (household_id)
    GROUP BY h.household_type, h.household_id
)
SELECT household_type,
       count(*)                                           AS households,
       sum(members)                                       AS residents,
       round(avg(members), 2)                             AS mean_size,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_households
FROM sizes
GROUP BY household_type
ORDER BY households DESC;
