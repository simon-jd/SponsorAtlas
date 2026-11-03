{{ config(tags=['dimensions']) }}
select soc_code as problem from {{ ref('dim_occupation') }} s
where soc_vintage = 'SOC2018' and (major_title is null
 or (soc_level <> 'major' and (parent_code is null or minor_title is null))
 or (soc_level = 'detailed' and broad_title is null))
union all
select s.soc_code from {{ ref('dim_occupation') }} s
left join {{ ref('dim_occupation') }} p on s.parent_code = p.soc_code
where s.soc_vintage = 'SOC2018' and s.soc_level <> 'major' and (p.soc_code is null or p.major_code <> s.major_code)
union all
select county_fips from {{ ref('dim_geography') }}
where (cbsa_status = 'REQUIRES_CT_BRIDGE' and cbsa_code is not null)
 or (cbsa_code is not null and cbsa_vintage <> 'CBSA2023-07')
 or (cbsa_status = 'NON_CBSA_2023' and cbsa_code is not null)
union all
select cast(date_key as varchar) from {{ ref('dim_date') }}
where fiscal_year <> calendar_year + case when calendar_month >= 10 then 1 else 0 end
 or (calendar_month = 10 and fiscal_quarter <> 1)
union all
select 'SOC_REFERENCE_ROW_MISSING_OR_EXTRA' from (
 select coalesce(d.soc_code,r.soc_code) as soc_code
 from {{ ref('dim_occupation') }} d full join {{ ref('ref_soc_2018') }} r
   on d.soc_code=r.soc_code and d.soc_vintage='SOC2018'
 where d.soc_code is null or (r.soc_code is null and d.soc_vintage='SOC2018')
) missing
union all
select 'INCORRECT_PARENT_LEVEL' from {{ ref('dim_occupation') }} s
join {{ ref('dim_occupation') }} p on s.parent_code=p.soc_code
where s.soc_vintage='SOC2018' and ((s.soc_level='detailed' and p.soc_level<>'broad')
 or (s.soc_level='broad' and p.soc_level<>'minor')
 or (s.soc_level='minor' and p.soc_level<>'major'))
union all
select soc_code from {{ ref('dim_occupation') }}
where soc_vintage='OEWS_May2025' and
 ((classification_type='OEWS_SPECIFIC' and (major_title is null or major_code is null))
 or (classification_type='AGGREGATE_TOTAL' and (major_code is not null or soc_code<>'00-0000')))
