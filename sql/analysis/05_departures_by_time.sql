-- title: Departures through the day
-- description: Trips starting in each 15-minute window, split by mode - the city's rush hours.
-- chart: stacked
-- x: time
-- y: walk,cycle,bus,train,car
WITH binned AS (
    SELECT depart_min / 15 * 15 AS slot, mode FROM mv_trips
)
SELECT to_char(make_time(s.slot / 60, s.slot % 60, 0), 'HH24:MI') AS time,
       count(b.mode) FILTER (WHERE b.mode = 'walk')  AS walk,
       count(b.mode) FILTER (WHERE b.mode = 'cycle') AS cycle,
       count(b.mode) FILTER (WHERE b.mode = 'bus')   AS bus,
       count(b.mode) FILTER (WHERE b.mode = 'train') AS train,
       count(b.mode) FILTER (WHERE b.mode = 'car')   AS car
FROM generate_series(0, 1425, 15) AS s(slot)
LEFT JOIN binned b USING (slot)
GROUP BY s.slot
ORDER BY s.slot;
