-- Archive FY is a label; survey reference is May calendar year. Full source strata retained.
with mapped as (
 {{ file_rows('bls', ['area_code', 'area_title', 'area_type', 'state_raw', 'naics', 'naics_title', 'industry_group', 'ownership_code', 'soc_code_raw', 'occupation_title', 'occupation_group', 'total_employment_raw', 'hourly_mean_wage_raw', 'annual_mean_wage_raw', 'hourly_p10_wage_raw', 'hourly_p25_wage_raw', 'hourly_median_wage_raw', 'hourly_p75_wage_raw', 'hourly_p90_wage_raw', 'annual_p10_wage_raw', 'annual_p25_wage_raw', 'annual_median_wage_raw', 'annual_p75_wage_raw', 'annual_p90_wage_raw', 'employment_prse_raw', 'jobs_per_1000_raw', 'location_quotient_raw', 'pct_total_raw', 'pct_reporting_raw', 'mean_prse_raw', 'annual_flag', 'hourly_flag']) }}
)
select *,fiscal_year as survey_year,'May' as survey_month,
 {{ normalize_soc('soc_code_raw') }} as soc_code,
 {{ integer_value('total_employment_raw') }} as total_employment,
 {{ number_value('hourly_mean_wage_raw') }} as hourly_mean_wage,
 {{ number_value('annual_mean_wage_raw') }} as annual_mean_wage,
 {{ number_value('hourly_p10_wage_raw') }} as hourly_p10_wage,
 {{ number_value('hourly_p25_wage_raw') }} as hourly_p25_wage,
 {{ number_value('hourly_median_wage_raw') }} as hourly_median_wage,
 {{ number_value('hourly_p75_wage_raw') }} as hourly_p75_wage,
 {{ number_value('hourly_p90_wage_raw') }} as hourly_p90_wage,
 {{ number_value('annual_p10_wage_raw') }} as annual_p10_wage,
 {{ number_value('annual_p25_wage_raw') }} as annual_p25_wage,
 {{ number_value('annual_median_wage_raw') }} as annual_median_wage,
 {{ number_value('annual_p75_wage_raw') }} as annual_p75_wage,
 {{ number_value('annual_p90_wage_raw') }} as annual_p90_wage,
 {{ number_value('employment_prse_raw') }} as employment_prse,
 {{ number_value('jobs_per_1000_raw') }} as jobs_per_1000,
 {{ number_value('location_quotient_raw') }} as location_quotient,
 {{ number_value('pct_total_raw') }} as pct_total,
 {{ number_value('pct_reporting_raw') }} as pct_reporting,
 {{ number_value('mean_prse_raw') }} as mean_prse,
 case when annual_mean_wage_raw in ('*','#','**','~') then 'SUPPRESSED_OR_TOP_CODED'
 when {{ number_value('annual_mean_wage_raw') }} is null then 'INVALID_OR_MISSING' else 'REPORTED' end as wage_status
from mapped
