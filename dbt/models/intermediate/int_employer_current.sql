{{ config(materialized='table', schema='INTERMEDIATE', tags=['dimensions']) }}
-- Latest observed fiscal year first; richer LCA then source key break ties.
-- This is an observation selection policy, never a historical effective date.
with ranked as (
 select *, row_number() over (
   partition by employer_id
   order by is_source_active desc, source_fiscal_year desc,
     case when source_system = 'lca' then 0 else 1 end,
     source_system, source_employer_key
 ) as selection_rank,
 max(case when is_source_active then 1 else 0 end) over(partition by employer_id) as has_active_source
 from {{ source('dimensions_input','employer_sources') }}
 where employer_id is not null
)
select employer_id, source_name as employer_name, state, country, city, postcode,
 source_system as attribute_source_system, source_employer_key as attribute_source_key,
 source_fiscal_year, source_observation_period, resolution_status, match_method,
 has_active_source = 1 as is_source_active,
 'UNVALIDATED_HUMAN_LABELS_REQUIRED' as identity_quality_status
from ranked where selection_rank = 1
