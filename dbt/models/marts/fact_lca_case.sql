{{ config(materialized='incremental', incremental_strategy='merge', unique_key='case_number',
 schema='MARTS', on_schema_change='fail', tags=['facts'],
 cluster_by=(['fiscal_year', 'worksite_state'] if target.type == 'snowflake'
             and var('task10_enable_clustering', false) else none)) }}
-- Reconsider the retained source history, so a late old record never overwrites a newer winner.
with winners as (
 select * from {{ ref('int_lca_case_candidates') }}
 where nullif(trim(case_number),'') is not null
 qualify row_number() over(partition by case_number order by winner_rank)=1
), history as (
 select w.case_number,count(d.employer_key) as matching_versions,max(d.employer_key) as employer_key
 from winners w left join {{ ref('dim_employer') }} d
 on w.employer_id=d.employer_id and d.valid_from<=cast(w.decision_date as timestamp)
 and (d.valid_to is null or d.valid_to>=cast(w.decision_date as timestamp)+interval '1 day')
 group by w.case_number
), first_capture as (
 select employer_id,min(valid_from) as first_valid_from from {{ ref('dim_employer') }} group by employer_id
)
select w.case_number,w.case_status,w.case_status like 'Certified%' as is_certified,
 w.visa_class,w.job_title,w.employer_name as source_employer_name,w.source_employer_key,
 w.employer_id,case when h.matching_versions=1 then h.employer_key end as employer_key,
 case when w.employer_id is null then 'UNRESOLVED_SOURCE_EMPLOYER'
 when w.decision_date is null then 'SOURCE_FACT_DATE_MISSING'
 when cast(w.decision_date as timestamp)+interval '1 day'<=f.first_valid_from then 'PRE_SNAPSHOT_HISTORY_UNKNOWN'
 when h.matching_versions=1 then 'MATCHED_CAPTURE_INTERVAL'
 else 'DATE_PRECISION_OR_INTERVAL_UNKNOWN' end as employer_history_status,
 'DATE_ONLY' as fact_time_precision,w.occupation_key,w.geography_key,
 d.date_key as decision_date_key,w.decision_date,w.received_date,w.begin_date,w.end_date,
 w.soc_code_raw,w.soc_code,w.occupation_mapping_status,w.county_mapping_status,
 w.wage_amount,w.wage_unit,cast(w.annual_wage as decimal(24,2)) as annual_wage,
 cast(w.worker_positions as decimal(38,0)) as worker_positions,
 w.source_system,w.source_release,w.source_sha256,w.source_url,w.source_row_number,w.fiscal_year,
 w.worksite_state,
 cast(w.source_occurrence_count as bigint) as source_occurrence_count,
 case when w.chronological_tie_count>1 then 'SAME_DATE_TIE_REVIEW_REQUIRED' else 'UNAMBIGUOUS_LATEST' end as latest_selection_status
from winners w join history h using(case_number)
left join first_capture f on w.employer_id=f.employer_id
left join {{ ref('dim_date') }} d on w.decision_date=d.calendar_date
