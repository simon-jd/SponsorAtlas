-- DuckDB reconciliation contract. lca/postings/wages are the manifest-verified
-- public Parquet projections. cube_results is actual authenticated Cube REST.
-- native_results must come from executed DAX/Tableau exports, never this SQL.
-- Each side uses queries.json; no global time filter conflates FY/survey year.
CREATE OR REPLACE TEMP VIEW sql_oracle AS
SELECT 'case_count' metric, count(*)::DOUBLE value FROM lca
UNION ALL
SELECT 'certified_rate', sum(CASE WHEN is_certified THEN 1 ELSE 0 END)::DOUBLE
  / nullif(count(*), 0) FROM lca
UNION ALL
SELECT 'wage_p75', quantile_cont(annual_wage, .75) FROM lca
UNION ALL
SELECT 'active_posting_days', sum(CASE WHEN is_active THEN 1 ELSE 0 END)::DOUBLE FROM postings
UNION ALL
SELECT 'oews_median', CASE WHEN count(*) = 1 THEN max(annual_median_wage) END
FROM wages WHERE soc = '15-1252' AND area = '1:99' AND survey_year = 2025;

-- Missing values and missing engines FAIL. Null equals null only when an
-- executed source row exists; missing native observations cannot pass as NULL.
SELECT o.metric, o.value sql_oracle, c.value cube_rest,
       p.value powerbi_dax, t.value tableau,
       CASE WHEN c.metric IS NOT NULL AND p.metric IS NOT NULL AND t.metric IS NOT NULL
         AND ((o.value IS NULL AND c.value IS NULL) OR abs(o.value-c.value) <= 1e-8)
         AND ((o.value IS NULL AND p.value IS NULL) OR abs(o.value-p.value) <= 1e-8)
         AND ((o.value IS NULL AND t.value IS NULL) OR abs(o.value-t.value) <= 1e-8)
         THEN 'PASS' ELSE 'FAIL' END status
FROM sql_oracle o
LEFT JOIN cube_results c USING(metric)
LEFT JOIN native_results p ON p.metric=o.metric AND p.engine='powerbi_dax'
LEFT JOIN native_results t ON t.metric=o.metric AND t.engine='tableau'
ORDER BY o.metric;
