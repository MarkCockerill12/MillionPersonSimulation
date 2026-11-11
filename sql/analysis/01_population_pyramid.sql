-- title: Population pyramid
-- description: Residents by 5-year age band and sex. Male counts are negated so the chart mirrors.
-- chart: pyramid
-- x: band
-- y: female,male
WITH aged AS (
    SELECT sex, LEAST(date_part('year', age(DATE '2026-01-01', date_of_birth))::int / 5 * 5, 90) AS lo
    FROM persons
)
SELECT CASE WHEN lo = 90 THEN '90+' ELSE lo || '-' || (lo + 4) END AS band,
       count(*) FILTER (WHERE sex = 'F')  AS female,
       -count(*) FILTER (WHERE sex = 'M') AS male
FROM aged
GROUP BY lo
ORDER BY lo DESC;
