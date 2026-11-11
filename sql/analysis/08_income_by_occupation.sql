-- title: Pay distribution by occupation
-- description: Income quartiles per occupation (percentile_cont) compared with the reference median salary.
-- chart: range
-- x: title
-- y: p25,median,p75
SELECT o.title,
       o.sector,
       count(*)                                                                      AS workers,
       round((percentile_cont(0.25) WITHIN GROUP (ORDER BY p.annual_income))::numeric) AS p25,
       round((percentile_cont(0.50) WITHIN GROUP (ORDER BY p.annual_income))::numeric) AS median,
       round((percentile_cont(0.75) WITHIN GROUP (ORDER BY p.annual_income))::numeric) AS p75,
       o.median_salary                                                               AS reference_median
FROM persons p
JOIN occupations o USING (occupation_id)
GROUP BY o.occupation_id, o.title, o.sector, o.median_salary
ORDER BY median DESC;
