with mapped as (
 {{ file_rows('lca', ['case_number','visa_class','trade_name_dba','employer_country','case_status','received_date_raw','decision_date_raw','begin_date_raw','end_date_raw','employer_name','employer_city','employer_zip','employer_address','employer_fein','employer_state_raw','job_title','soc_code_raw','worksite_state_raw','worksite_county_raw','worksite_city_raw','worksite_postal_code_raw','wage_amount_raw','wage_unit','worker_positions_raw']) }}
), typed as (
 select *,
 {{ strict_date('received_date_raw') }} as received_date,
 {{ strict_date('decision_date_raw') }} as decision_date,
 {{ strict_date('begin_date_raw') }} as begin_date,
 {{ strict_date('end_date_raw') }} as end_date,
 {{ normalize_soc('soc_code_raw') }} as soc_code,
 upper(trim(employer_state_raw)) as employer_state,
 upper(trim(worksite_state_raw)) as worksite_state,
 {{ county_key('worksite_county_raw') }} as worksite_county,
 {{ county_key('worksite_city_raw') }} as worksite_city,
 -- First five digits of ZIP / ZIP+4; 4-digit values lost their leading zero in the source.
 case when {{ matches('trim(worksite_postal_code_raw)', '[0-9]{5}(-?[0-9]{4})?') }}
      then left(trim(worksite_postal_code_raw), 5)
      when {{ matches('trim(worksite_postal_code_raw)', '[0-9]{4}') }}
      then '0' || trim(worksite_postal_code_raw) end as worksite_zip5,
 {{ number_value('wage_amount_raw') }} as wage_amount,
 {{ integer_value('worker_positions_raw') }} as worker_positions
 from mapped
)
-- County comes from the source county name; when that is missing or unmatched, from the
-- worksite ZIP via the Census2020 ZCTA -> county seed (one county or >= 90% of the land,
-- same state only), then from the worksite city via the Census2020 place -> county seed
-- (single-county names only).
select t.*, md5(coalesce(employer_country,'')||'|'||coalesce(employer_name,'')||'|'||coalesce(employer_state,'')||'|'||coalesce(employer_fein,'')||'|'||coalesce(employer_city,'')||'|'||coalesce(employer_zip,'')) as source_employer_key, {{ annualize_wage('t.wage_amount','t.wage_unit') }} as annual_wage,
 s.state_fips,
 coalesce(c.county_fips, z.county_fips, p.county_fips) as county_fips,
 coalesce(c.geography_vintage, z.geography_vintage, p.geography_vintage) as geography_vintage,
 case when c.county_fips is not null then 'MATCHED'
      when z.county_fips is not null then 'MATCHED_FROM_ZIP'
      when p.county_fips is not null then 'MATCHED_FROM_CITY'
      when t.worksite_county is null then 'SOURCE_COUNTY_MISSING'
      else 'UNRESOLVED_COUNTY' end as geography_status,
 case when c.county_fips is not null then 'SOURCE_COUNTY'
      else coalesce(z.match_rule, p.match_rule) end as county_match_rule,
 c.county_fips is null and z.county_fips is null and p.county_fips is null as geo_unresolved,
 t.soc_code is null as soc_unresolved
from typed t
left join {{ ref('state_fips') }} s on s.state_code = t.worksite_state
left join {{ ref('county_fips') }} c
 on c.state_code = t.worksite_state and c.county_name = t.worksite_county
left join {{ ref('zcta_county_fips') }} z
 on z.zcta = t.worksite_zip5 and z.state_fips = s.state_fips
left join {{ ref('place_county_fips') }} p
 on p.state_code = t.worksite_state and p.place_name = t.worksite_city
