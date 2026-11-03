{{ config(tags=['staging']) }}
-- Missing SOC, county, USCIS employer name and USCIS state are not errors here: those rows
-- carry soc_unresolved / geo_unresolved / employer_name_missing / state_unresolved and
-- their share is bounded by staging_flag_limits.
select 'lca' as source_system, source_release, source_row_number,
 case when received_date is null or decision_date is null or begin_date is null or end_date is null or begin_date > end_date or received_date > decision_date then 'INVALID_DATE'
      when wage_amount is null or wage_amount <= 0 or annual_wage is null then 'INVALID_WAGE'
      when state_fips is null then 'STATE_UNRESOLVED'
      when substr(county_fips,1,2) != state_fips or not {{ matches('county_fips', '[0-9]{5}') }} then 'INVALID_FIPS'
      when geo_unresolved != (county_fips is null) or soc_unresolved != (soc_code is null) then 'INCONSISTENT_FLAG'
      else 'INVALID_WORKER_POSITIONS_OR_IDENTITY' end as error_code
from {{ ref('stg_lca') }}
where received_date is null or decision_date is null or begin_date is null or end_date is null
 or begin_date > end_date or received_date > decision_date
 or wage_amount is null or wage_amount <= 0 or annual_wage is null
 or state_fips is null
 or substr(county_fips,1,2) != state_fips
 or not {{ matches('county_fips', '[0-9]{5}') }}
 or geo_unresolved != (county_fips is null) or soc_unresolved != (soc_code is null)
 or worker_positions is null or worker_positions < 0 or worker_positions != floor(worker_positions)
 or employer_name is null or case_number is null
union all
select 'uscis', source_release, source_row_number, 'INVALID_FY_COUNTS_OR_FLAG'
from {{ ref('stg_uscis') }}
where source_fiscal_year is null or source_fiscal_year != fiscal_year
 or employer_name_missing != (employer_name is null) or state_unresolved != (state_fips is null)
 or initial_approvals is null or initial_denials is null
 or continuing_approvals is null or continuing_denials is null
 or initial_approvals < 0 or initial_denials < 0 or continuing_approvals < 0 or continuing_denials < 0
 or initial_approvals != floor(initial_approvals) or initial_denials != floor(initial_denials)
 or continuing_approvals != floor(continuing_approvals) or continuing_denials != floor(continuing_denials)
union all
select 'bls', source_release, source_row_number, 'INVALID_SOC_OR_WAGE'
from {{ ref('stg_bls') }}
where soc_code is null or area_code is null or area_type is null
 or wage_status = 'INVALID_OR_MISSING'
 or annual_mean_wage < 0 or total_employment < 0
 or (hourly_mean_wage_raw not in ('*','#','**') and hourly_mean_wage is null)
 or (total_employment_raw not in ('*','#','**') and total_employment is null)
