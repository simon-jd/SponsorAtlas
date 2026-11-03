{{ config(materialized='table', schema='MARTS', tags=['dimensions']) }}
select md5(s.soc_vintage||'|'||s.soc_code) as occupation_key,
 s.soc_code, s.soc_title, s.soc_level, s.soc_vintage,
 s.major_code, major.soc_title as major_title,
 s.minor_code, minor.soc_title as minor_title,
 s.broad_code, broad.soc_title as broad_title, s.parent_code,
 'STANDARD_SOC' as classification_type
from {{ ref('ref_soc_2018') }} s
left join {{ ref('ref_soc_2018') }} major on s.major_code = major.soc_code
left join {{ ref('ref_soc_2018') }} minor on s.minor_code = minor.soc_code
left join {{ ref('ref_soc_2018') }} broad on s.broad_code = broad.soc_code
union all
select md5(s.soc_vintage||'|'||s.soc_code), s.soc_code,s.soc_title,s.soc_level,s.soc_vintage,
 s.major_code,major.soc_title,cast(null as varchar),cast(null as varchar),
 cast(null as varchar),cast(null as varchar),s.major_code,s.classification_type
from {{ ref('ref_oews_2025') }} s
left join {{ ref('ref_soc_2018') }} major on s.major_code=major.soc_code
