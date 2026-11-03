with mapped as (
 {{ file_rows('uscis', ['source_fiscal_year_raw','employer_name','employer_tax_id','employer_state_raw','employer_city','employer_zip','initial_approvals_raw','initial_denials_raw','continuing_approvals_raw','continuing_denials_raw','naics_code']) }}
)
select m.*, md5(coalesce(employer_name,'')||'|'||coalesce(employer_state_raw,'')||'|'||coalesce(employer_tax_id,'')||'|'||coalesce(employer_city,'')||'|'||coalesce(employer_zip,'')) as source_employer_key, upper(trim(employer_state_raw)) as employer_state,
 s.state_fips,
 {{ integer_value('source_fiscal_year_raw') }} as source_fiscal_year,
 {{ integer_value('initial_approvals_raw') }} as initial_approvals,
 {{ integer_value('initial_denials_raw') }} as initial_denials,
 {{ integer_value('continuing_approvals_raw') }} as continuing_approvals,
 {{ integer_value('continuing_denials_raw') }} as continuing_denials,
 m.employer_name is null as employer_name_missing,
 s.state_fips is null as state_unresolved,
 not (
  nullif(trim(m.employer_state_raw), '') is null
  and nullif(trim(m.employer_city), '') is null
  and nullif(trim(m.employer_zip), '') is null
 ) as released
from mapped m left join {{ ref('state_fips') }} s on s.state_code = upper(trim(m.employer_state_raw))
