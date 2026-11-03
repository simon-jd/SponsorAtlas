{{ config(materialized='table',schema='INTERMEDIATE',tags=['facts']) }}
with scoped as (
 select *,md5(coalesce(cast(length(area_code) as varchar),'NULL')||':'||coalesce(area_code,'')
 ||'|'||coalesce(cast(length(area_title) as varchar),'NULL')||':'||coalesce(area_title,'')
 ||'|'||coalesce(cast(length(area_type) as varchar),'NULL')||':'||coalesce(area_type,'')
 ||'|'||coalesce(cast(length(state_raw) as varchar),'NULL')||':'||coalesce(state_raw,'')
 ||'|'||coalesce(cast(length(naics) as varchar),'NULL')||':'||coalesce(naics,'')
 ||'|'||coalesce(cast(length(naics_title) as varchar),'NULL')||':'||coalesce(naics_title,'')
 ||'|'||coalesce(cast(length(industry_group) as varchar),'NULL')||':'||coalesce(industry_group,'')
 ||'|'||coalesce(cast(length(ownership_code) as varchar),'NULL')||':'||coalesce(ownership_code,'')
 ||'|'||coalesce(cast(length(soc_code_raw) as varchar),'NULL')||':'||coalesce(soc_code_raw,'')
 ||'|'||coalesce(cast(length(occupation_title) as varchar),'NULL')||':'||coalesce(occupation_title,'')
 ||'|'||coalesce(cast(length(total_employment_raw) as varchar),'NULL')||':'||coalesce(total_employment_raw,'')
 ||'|'||coalesce(cast(length(hourly_mean_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_mean_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_mean_wage_raw) as varchar),'NULL')||':'||coalesce(annual_mean_wage_raw,'')
 ||'|'||coalesce(cast(length(hourly_p10_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_p10_wage_raw,'')
 ||'|'||coalesce(cast(length(hourly_p25_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_p25_wage_raw,'')
 ||'|'||coalesce(cast(length(hourly_median_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_median_wage_raw,'')
 ||'|'||coalesce(cast(length(hourly_p75_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_p75_wage_raw,'')
 ||'|'||coalesce(cast(length(hourly_p90_wage_raw) as varchar),'NULL')||':'||coalesce(hourly_p90_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_p10_wage_raw) as varchar),'NULL')||':'||coalesce(annual_p10_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_p25_wage_raw) as varchar),'NULL')||':'||coalesce(annual_p25_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_median_wage_raw) as varchar),'NULL')||':'||coalesce(annual_median_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_p75_wage_raw) as varchar),'NULL')||':'||coalesce(annual_p75_wage_raw,'')
 ||'|'||coalesce(cast(length(annual_p90_wage_raw) as varchar),'NULL')||':'||coalesce(annual_p90_wage_raw,'')
 ||'|'||coalesce(cast(length(employment_prse_raw) as varchar),'NULL')||':'||coalesce(employment_prse_raw,'')
 ||'|'||coalesce(cast(length(jobs_per_1000_raw) as varchar),'NULL')||':'||coalesce(jobs_per_1000_raw,'')
 ||'|'||coalesce(cast(length(location_quotient_raw) as varchar),'NULL')||':'||coalesce(location_quotient_raw,'')
 ||'|'||coalesce(cast(length(pct_total_raw) as varchar),'NULL')||':'||coalesce(pct_total_raw,'')
 ||'|'||coalesce(cast(length(pct_reporting_raw) as varchar),'NULL')||':'||coalesce(pct_reporting_raw,'')
 ||'|'||coalesce(cast(length(mean_prse_raw) as varchar),'NULL')||':'||coalesce(mean_prse_raw,'')
 ||'|'||coalesce(cast(length(annual_flag) as varchar),'NULL')||':'||coalesce(annual_flag,'')
 ||'|'||coalesce(cast(length(hourly_flag) as varchar),'NULL')||':'||coalesce(hourly_flag,'')) as estimate_fingerprint
 from {{ ref('stg_bls') }}
 where industry_group='cross-industry' and naics='000000' and ownership_code='1235'
), diagnosed as (
 select *,count(distinct estimate_fingerprint) over(partition by soc_code,area_type,area_code,survey_year) as estimate_variants,
 count(*) over(partition by soc_code,area_type,area_code,survey_year) as source_occurrence_count,
 min(source_row_number) over(partition by soc_code,area_type,area_code,survey_year) as source_first_row_number,
 max(source_row_number) over(partition by soc_code,area_type,area_code,survey_year) as source_last_row_number,
 row_number() over(partition by soc_code,area_type,area_code,survey_year
 order by case when occupation_group='detailed' then 0 else 1 end,source_sha256,source_row_number) as canonical_rank
 from scoped
)
select * from diagnosed
