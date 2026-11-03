{{ config(materialized='incremental',incremental_strategy='merge',
 unique_key=['soc_code','area_type','area_code','survey_year'],schema='MARTS',on_schema_change='fail',tags=['facts']) }}
-- Duplicate estimates must be identical across all31 non-O_GROUP original fields.
-- Conflicts remain in the diagnostic relation and make facts_bls_conflicts fail.
select b.soc_code,b.area_type,b.area_code,b.survey_year,b.survey_month,
 o.occupation_key,g.geography_key,d.date_key as survey_date_key,
 case when o.classification_type='STANDARD_SOC' then 'SOC2018' else 'OEWS_May2025' end as occupation_vintage,
 b.occupation_title,b.occupation_group,b.area_title,b.industry_group,b.naics,b.ownership_code,
 b.annual_mean_wage,b.hourly_mean_wage,b.annual_median_wage,b.hourly_median_wage,
 b.annual_p10_wage,b.annual_p25_wage,b.annual_p75_wage,b.annual_p90_wage,
 b.hourly_p10_wage,b.hourly_p25_wage,b.hourly_p75_wage,b.hourly_p90_wage,
 cast(b.total_employment as decimal(38,0)) as total_employment,
 b.annual_median_wage_raw,b.hourly_median_wage_raw,b.total_employment_raw,b.annual_flag,b.hourly_flag,b.wage_status,
 b.source_release,b.source_sha256,b.source_url,b.source_row_number,
 cast(b.source_occurrence_count as bigint) as source_occurrence_count,
 b.source_first_row_number,b.source_last_row_number,b.payload_json as canonical_payload_json
from {{ ref('int_bls_benchmark_candidates') }} b
left join {{ ref('dim_occupation') }} o on b.soc_code=o.soc_code
left join {{ ref('dim_geography') }} g on b.area_type=g.area_type and b.area_code=g.area_code
 and g.geography_vintage='OEWS_May2025'
left join {{ ref('dim_date') }} d on d.calendar_date=cast(cast(b.survey_year as varchar)||'-05-01' as date)
where b.estimate_variants=1 and b.canonical_rank=1
