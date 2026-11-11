-- title: Commute mode share
-- description: How people get to work, school or university: share of morning commutes by mode, with median straight-line distance and mean journey time.
-- chart: bar
-- x: mode
-- y: trips
SELECT mode,
       count(*)                                                                     AS trips,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1)                           AS share_pct,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY straight_line_m))::numeric / 1000, 2) AS median_km,
       round(avg(arrive_min - depart_min), 1)                                       AS avg_minutes
FROM mv_trips
WHERE trip_seq = 0 AND to_type IN ('work', 'school', 'university')
GROUP BY mode
ORDER BY trips DESC;
