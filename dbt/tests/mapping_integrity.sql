-- Selection dependency: {{ ref('stg_lca') }} {{ ref('stg_uscis') }} {{ ref('stg_bls') }}
{{ config(tags=['staging']) }}
with mappings as (
 select 'lca' as source_system, fiscal_year, source_column, standard_column, disposition, reason from {{ ref('lca_column_map') }}
 union all
 select source_system,fiscal_year,source_column,standard_column,disposition,reason from {{ ref('file_column_map') }}
)
select source_system,fiscal_year,source_column,'INVALID_OR_DUPLICATE_MAPPING' as error_code
from mappings
group by source_system,fiscal_year,source_column
having count(*) != 1 or max(disposition) not in ('mapped','excluded')
 or max(reason) is null or min(length(reason)) = 0
 or max(case when disposition = 'mapped' and (standard_column is null or standard_column = '') then 1 else 0 end) = 1
union all
select source_system,fiscal_year,standard_column,'DUPLICATE_STANDARD_COLUMN'
from mappings where disposition = 'mapped'
group by source_system,fiscal_year,standard_column having count(*) != 1
