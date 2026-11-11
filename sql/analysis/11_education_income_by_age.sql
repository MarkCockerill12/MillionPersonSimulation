-- title: Education premium by age
-- description: Median income of working adults by education level within each age decade (a pivot using aggregate FILTER clauses).
-- chart: line
-- x: age_band
-- y: secondary,upper_secondary,vocational,bachelors,masters,doctorate
WITH w AS (
    SELECT education_level,
           date_part('year', age(DATE '2026-01-01', date_of_birth))::int / 10 * 10 AS decade,
           annual_income::numeric AS income
    FROM persons
    WHERE employment_status IN ('employed', 'self_employed')
)
SELECT decade || 's' AS age_band,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'secondary'))       AS secondary,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'upper_secondary')) AS upper_secondary,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'vocational'))      AS vocational,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'bachelors'))      AS bachelors,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'masters'))        AS masters,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY income) FILTER (WHERE education_level = 'doctorate'))      AS doctorate
FROM w
WHERE decade BETWEEN 20 AND 60
GROUP BY decade
ORDER BY decade;
