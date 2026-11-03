{{ config(tags=['staging']) }}
-- Rows that cannot be resolved stay in staging with an explicit flag. This test fails
-- when a flagged share in any source/fiscal year exceeds its limit in staging_flag_limits.
-- USCIS rows with a blank state, city and ZIP are not released (user decision 2026-10-28)
-- and are left out of that share. The 5% limit itself is unchanged.
with flags as (
 select 'lca' as source_system, fiscal_year, 'geo_unresolved' as flag, geo_unresolved as flagged
 from {{ ref('stg_lca') }}
 union all
 select 'lca', fiscal_year, 'soc_unresolved', soc_unresolved from {{ ref('stg_lca') }}
 union all
 select 'uscis', fiscal_year, 'employer_name_missing', employer_name_missing from {{ ref('stg_uscis') }} where released
 union all
 select 'uscis', fiscal_year, 'state_unresolved', state_unresolved from {{ ref('stg_uscis') }} where released
), shares as (
 select source_system, fiscal_year, flag, count(*) as total_rows,
  sum(case when flagged then 1 else 0 end) as flagged_rows
 from flags group by source_system, fiscal_year, flag
)
select s.*, l.max_share
from shares s
left join {{ ref('staging_flag_limits') }} l
 on l.source_system = s.source_system and l.flag = s.flag
where l.max_share is null or s.flagged_rows > l.max_share * s.total_rows
