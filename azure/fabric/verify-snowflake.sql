SELECT COUNT(*) AS "case_count",
       AVG(CASE WHEN case_status IN ('Certified','Certified - Withdrawn')
                THEN 1.0 ELSE 0.0 END) AS "certified_rate",
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY annual_wage) AS "wage_p50",
       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY annual_wage) AS "wage_p90"
FROM ANALYTICS.MARTS.fact_lca_case AT (TIMESTAMP => '{snapshot}'::TIMESTAMP_TZ)
