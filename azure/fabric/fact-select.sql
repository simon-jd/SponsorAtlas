-- Canonical dbt fact_lca_case semantics. {{lakehouse}} is a validated identifier.
WITH mapped AS (
 SELECT l.*, x.employer_id, o.occupation_key, g.geography_key,
 CASE WHEN l.soc_code_raw IS NULL OR TRIM(l.soc_code_raw)='' THEN 'SOURCE_SOC_MISSING'
      WHEN l.soc_code IS NULL THEN 'SOURCE_SOC_MALFORMED'
      WHEN o.occupation_key IS NULL THEN 'SOC_NOT_IN_2018_REFERENCE' ELSE 'MATCHED' END AS occupation_mapping_status,
 CASE WHEN l.worksite_county IS NULL THEN 'SOURCE_COUNTY_MISSING'
      WHEN c.county_fips IS NULL THEN 'UNRESOLVED_COUNTY'
      WHEN g.geography_key IS NULL THEN 'DIMENSION_COUNTY_MAPPING_FAILED' ELSE 'MATCHED' END AS county_mapping_status
 FROM {{lakehouse}}.dbo.port_lca l
 LEFT JOIN {{lakehouse}}.dbo.port_employer_xref x
   ON l.source_system=x.source_system AND l.source_employer_key=x.source_employer_key
 LEFT JOIN MARTS.dim_occupation o ON l.soc_code=o.soc_code AND o.soc_vintage='SOC2018'
 LEFT JOIN {{lakehouse}}.dbo.port_county_fips c ON l.worksite_state=c.state_code AND l.worksite_county=c.county_name
 LEFT JOIN MARTS.dim_geography g ON c.county_fips=g.county_fips AND c.geography_vintage=g.county_reference_vintage
), ranked AS (
 SELECT m.*,
 COUNT_BIG(*) OVER(PARTITION BY case_number) AS source_occurrence_count,
 COUNT_BIG(*) OVER(PARTITION BY case_number,decision_date,received_date,fiscal_year) AS chronological_tie_count,
 ROW_NUMBER() OVER(PARTITION BY case_number ORDER BY
   CASE WHEN decision_date IS NULL THEN 1 ELSE 0 END,decision_date DESC,
   CASE WHEN received_date IS NULL THEN 1 ELSE 0 END,received_date DESC,
   fiscal_year DESC,source_release DESC,source_sha256,source_row_number) AS winner_rank
 FROM mapped m
), winners AS (
 SELECT * FROM ranked WHERE winner_rank=1 AND NULLIF(TRIM(case_number),'') IS NOT NULL
), history AS (
 SELECT w.case_number,COUNT(d.employer_key) AS matching_versions,MAX(d.employer_key) AS employer_key
 FROM winners w LEFT JOIN MARTS.dim_employer d ON w.employer_id=d.employer_id
 AND d.valid_from<=CAST(w.decision_date AS datetime2(6))
 AND (d.valid_to IS NULL OR d.valid_to>=DATEADD(day,1,CAST(w.decision_date AS datetime2(6))))
 GROUP BY w.case_number
), first_capture AS (
 SELECT employer_id,MIN(valid_from) AS first_valid_from FROM MARTS.dim_employer GROUP BY employer_id
)
SELECT w.case_number,w.case_status,CAST(CASE WHEN w.case_status LIKE 'Certified%' THEN 1 ELSE 0 END AS bit) AS is_certified,
 w.visa_class,w.job_title,w.employer_name AS source_employer_name,w.source_employer_key,
 w.employer_id,CASE WHEN h.matching_versions=1 THEN h.employer_key END AS employer_key,
 CASE WHEN w.employer_id IS NULL THEN 'UNRESOLVED_SOURCE_EMPLOYER'
 WHEN w.decision_date IS NULL THEN 'SOURCE_FACT_DATE_MISSING'
 WHEN DATEADD(day,1,CAST(w.decision_date AS datetime2(6)))<=f.first_valid_from THEN 'PRE_SNAPSHOT_HISTORY_UNKNOWN'
 WHEN h.matching_versions=1 THEN 'MATCHED_CAPTURE_INTERVAL'
 ELSE 'DATE_PRECISION_OR_INTERVAL_UNKNOWN' END AS employer_history_status,
 'DATE_ONLY' AS fact_time_precision,w.occupation_key,w.geography_key,
 d.date_key AS decision_date_key,w.decision_date,w.received_date,w.begin_date,w.end_date,
 w.soc_code_raw,w.soc_code,w.occupation_mapping_status,w.county_mapping_status,
 w.wage_amount,w.wage_unit,CAST(w.annual_wage AS decimal(24,2)) AS annual_wage,
 CAST(w.worker_positions AS decimal(38,0)) AS worker_positions,
 w.source_system,w.source_release,w.source_sha256,w.source_url,w.source_row_number,w.fiscal_year,
 w.worksite_state,CAST(w.source_occurrence_count AS bigint) AS source_occurrence_count,
 CASE WHEN w.chronological_tie_count>1 THEN 'SAME_DATE_TIE_REVIEW_REQUIRED' ELSE 'UNAMBIGUOUS_LATEST' END AS latest_selection_status
FROM winners w JOIN history h ON w.case_number=h.case_number
LEFT JOIN first_capture f ON w.employer_id=f.employer_id
LEFT JOIN MARTS.dim_date d ON w.decision_date=d.calendar_date
