{{ config(materialized='table', schema='MARTS', tags=['dimensions']) }}
select dbt_scd_id as employer_key, employer_id, employer_name,
 state, country, city, postcode, attribute_source_system, attribute_source_key,
 source_fiscal_year, source_observation_period, resolution_status, match_method,
 identity_quality_status, is_source_active,
 dbt_valid_from as valid_from, dbt_valid_to as valid_to,
 dbt_valid_to is null as is_current,
 'CAPTURE_TIME_UTC' as validity_semantics
from {{ ref('snap_employer') }}
