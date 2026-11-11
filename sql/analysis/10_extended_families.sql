-- title: Extended family networks
-- description: Recursive CTE walking the family graph up to 3 hops (parents, grandparents, siblings, cousins-in-law…) from a 2,000-person sample. How many relatives does each person have, and across how many addresses?
-- chart: bar
-- x: relatives
-- y: people
WITH RECURSIVE seed AS (
    SELECT person_id FROM persons WHERE person_id % 500 = 0
), walk (root, person_id, depth, path) AS (
    SELECT person_id, person_id, 0, ARRAY[person_id] FROM seed
    UNION ALL
    SELECT w.root, r.related_person_id, w.depth + 1, w.path || r.related_person_id
    FROM walk w
    JOIN relationships r ON r.person_id = w.person_id
    WHERE w.depth < 3 AND r.related_person_id <> ALL (w.path)
), reach AS (
    SELECT w.root,
           count(DISTINCT w.person_id) - 1  AS relatives,
           count(DISTINCT p.household_id)   AS addresses
    FROM walk w
    JOIN persons p ON p.person_id = w.person_id
    GROUP BY w.root
)
SELECT CASE WHEN relatives >= 10 THEN '10+' ELSE relatives::text END AS relatives,
       count(*)                                                     AS people,
       round(avg(addresses), 2)                                     AS avg_addresses
FROM reach
GROUP BY 1, LEAST(relatives, 10)
ORDER BY LEAST(relatives, 10);
