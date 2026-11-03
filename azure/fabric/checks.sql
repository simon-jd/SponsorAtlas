-- Each query returns a violation count; the runner requires exactly zero.
SELECT COUNT_BIG(*) AS violations FROM (SELECT case_number FROM MARTS.fact_lca_case GROUP BY case_number HAVING COUNT_BIG(*)<>1 OR case_number IS NULL OR TRIM(case_number)='') q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT employer_key FROM MARTS.dim_employer GROUP BY employer_key HAVING COUNT_BIG(*)<>1 OR employer_key IS NULL) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT occupation_key FROM MARTS.dim_occupation GROUP BY occupation_key HAVING COUNT_BIG(*)<>1 OR occupation_key IS NULL) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT geography_key FROM MARTS.dim_geography GROUP BY geography_key HAVING COUNT_BIG(*)<>1 OR geography_key IS NULL) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT date_key FROM MARTS.dim_date GROUP BY date_key HAVING COUNT_BIG(*)<>1 OR date_key IS NULL) q;
SELECT COUNT_BIG(*) AS violations FROM MARTS.dim_employer WHERE valid_to<=valid_from OR validity_semantics<>'CAPTURE_TIME_UTC';
SELECT COUNT_BIG(*) AS violations FROM MARTS.dim_employer a JOIN MARTS.dim_employer b ON a.employer_id=b.employer_id AND a.employer_key<b.employer_key AND (a.valid_to IS NULL OR b.valid_from<a.valid_to) AND (b.valid_to IS NULL OR a.valid_from<b.valid_to);
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_employer d ON f.employer_key=d.employer_key WHERE f.employer_key IS NOT NULL AND (d.employer_key IS NULL OR f.employer_id<>d.employer_id OR d.valid_from>CAST(f.decision_date AS datetime2(6)) OR d.valid_to<DATEADD(day,1,CAST(f.decision_date AS datetime2(6))) OR f.employer_history_status<>'MATCHED_CAPTURE_INTERVAL');
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case WHERE (employer_history_status='MATCHED_CAPTURE_INTERVAL' AND employer_key IS NULL) OR fact_time_precision<>'DATE_ONLY';
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_occupation d ON f.occupation_key=d.occupation_key WHERE f.occupation_key IS NOT NULL AND d.occupation_key IS NULL;
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_geography d ON f.geography_key=d.geography_key WHERE f.geography_key IS NOT NULL AND d.geography_key IS NULL;
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_date d ON f.decision_date_key=d.date_key WHERE (f.decision_date IS NOT NULL AND (d.date_key IS NULL OR f.decision_date<>d.calendar_date));
SELECT COUNT_BIG(*) AS violations FROM MARTS.fact_lca_case WHERE case_status IS NULL OR case_status NOT IN ('Certified','Certified - Withdrawn','Denied','Withdrawn') OR source_sha256 IS NULL OR LEN(source_sha256)<>64 OR source_row_number<1 OR source_occurrence_count<1 OR source_release NOT IN ('FY2024_Q4','FY2025_Q4');
