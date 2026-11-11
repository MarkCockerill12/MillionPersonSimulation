-- title: Where is everyone?
-- description: Head-count by activity at the top of every hour, from a range join of a time series against activity intervals. Anyone not inside an activity is travelling.
-- chart: stacked
-- x: hour
-- y: home,work,education,errands,leisure,travelling
WITH hours AS (
    SELECT h, h * 60 AS m FROM generate_series(0, 23) AS h
), present AS (
    SELECT hr.h,
           CASE WHEN a.activity_type IN ('school', 'university')                  THEN 'education'
                WHEN a.activity_type IN ('shopping', 'food_drink', 'healthcare')  THEN 'errands'
                ELSE a.activity_type END AS what,
           count(*) AS n
    FROM hours hr
    JOIN activities a ON hr.m >= a.start_min AND hr.m < a.end_min
    GROUP BY 1, 2
)
SELECT lpad(h::text, 2, '0') || ':00'          AS hour,
       sum(n) FILTER (WHERE what = 'home')      AS home,
       sum(n) FILTER (WHERE what = 'work')      AS work,
       sum(n) FILTER (WHERE what = 'education') AS education,
       sum(n) FILTER (WHERE what = 'errands')   AS errands,
       sum(n) FILTER (WHERE what = 'leisure')   AS leisure,
       (SELECT count(*) FROM persons) - sum(n)  AS travelling
FROM present
GROUP BY h
ORDER BY h;
