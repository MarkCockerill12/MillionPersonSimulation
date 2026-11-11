-- title: District league table
-- description: Every district ranked on income, density and employment using window functions (RANK, NTILE, share of total).
-- chart: table
SELECT name,
       postcode_district,
       population,
       density_per_km2,
       round(median_income::numeric)                          AS median_income,
       employment_rate_pct,
       car_commute_pct,
       RANK()   OVER (ORDER BY median_income DESC)            AS income_rank,
       NTILE(5) OVER (ORDER BY density_per_km2)               AS density_quintile,
       round(100.0 * population / sum(population) OVER (), 2) AS pct_of_city
FROM mv_district_stats
ORDER BY income_rank;
