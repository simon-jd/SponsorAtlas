{{ config(materialized='table', schema='MARTS', tags=['dimensions']) }}
with counties as (
 select county_fips, state_code, min(county_name) as county_name,
        geography_vintage as county_reference_vintage
 from {{ ref('county_fips') }} group by county_fips, state_code, geography_vintage
 union all
 select county_fips,state_code,county_name,county_reference_vintage
 from {{ ref('ref_county_2023') }}
)
select md5(c.county_reference_vintage||'|'||c.county_fips) as geography_key,
 c.county_fips, substr(c.county_fips,1,2) as state_fips, c.state_code,
 c.county_name, c.county_reference_vintage,
 case when c.state_code = 'CT' and c.county_reference_vintage = 'Census2020' then null else b.cbsa_code end as cbsa_code,
 case when c.state_code = 'CT' and c.county_reference_vintage = 'Census2020' then null else b.cbsa_title end as cbsa_title,
 case when c.state_code = 'CT' and c.county_reference_vintage = 'Census2020' then null else b.cbsa_type end as cbsa_type,
 case when g.county_fips is not null then 'CBSA2023-07' else null end as cbsa_vintage,
 case when c.state_code = 'CT' and c.county_reference_vintage = 'Census2020' then 'REQUIRES_CT_BRIDGE'
      when g.county_fips is null then 'OUTSIDE_2023_REFERENCE'
      when b.cbsa_code is null then 'NON_CBSA_2023'
      when c.county_reference_vintage = 'Census2020' then 'CODE_CONTINUITY_TO_2023'
      else 'MATCHED_2023' end as cbsa_status,
 g.county_internal_latitude, g.county_internal_longitude,
 case when g.county_fips is not null then 'Census2023' else null end as coordinate_reference_vintage,
 'COUNTY_INTERNAL_POINT_NOT_WORKSITE' as coordinate_semantics,
 'COUNTY' as geography_level,cast(null as varchar) as area_type,cast(null as varchar) as area_code,
 c.county_name as geography_title,c.county_reference_vintage as geography_vintage
from counties c
left join {{ ref('ref_county_2023') }} g on c.county_fips = g.county_fips
left join {{ ref('ref_county_cbsa_2023') }} b on c.county_fips = b.county_fips
union all
select md5(a.geography_vintage||'|'||a.area_type||'|'||a.area_code),
 cast(null as varchar),cast(null as varchar),a.state_raw,
 cast(null as varchar),cast(null as varchar),cast(null as varchar),cast(null as varchar),
 cast(null as varchar),cast(null as varchar),'BLS_AREA_NOT_COUNTY',
 cast(null as decimal(10,7)),cast(null as decimal(10,7)),cast(null as varchar),
 'NO_WORKSITE_COORDINATE',
 case a.area_type when '1' then 'NATIONAL' when '2' then 'STATE' when '3' then 'TERRITORY'
 when '4' then 'MSA' when '6' then 'BLS_NONMETROPOLITAN' end,
 a.area_type,a.area_code,a.area_title,a.geography_vintage
from {{ ref('ref_bls_areas_2025') }} a
