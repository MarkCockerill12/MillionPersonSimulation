-- title: Extended family reach
-- description: Recursive CTE walking the family graph up to 3 hops from a 2,000-person sample, counting how many separate addresses each family spans.
-- chart: bar
-- x: addresses_spanned
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
    SELECT w.root, count(DISTINCT p.household_id) AS addresses
    FROM walk w
    JOIN persons p ON p.person_id = w.person_id
    GROUP BY w.root
)
SELECT addresses AS addresses_spanned, count(*) AS people
FROM reach
GROUP BY addresses
ORDER BY addresses;
