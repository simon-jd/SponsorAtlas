-- Run inside the same Warehouse snapshot transaction as checks and lineage.
SELECT COUNT_BIG(*) AS case_count,
       AVG(CAST(CASE WHEN case_status IN ('Certified','Certified - Withdrawn')
                     THEN 1 ELSE 0 END AS decimal(18,10))) AS certified_rate,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY annual_wage) AS wage_p50,
       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY annual_wage) AS wage_p90
FROM MARTS.fact_lca_case;
