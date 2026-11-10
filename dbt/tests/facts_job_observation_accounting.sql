{{ config(tags=['facts']) }}
-- Per UTC observation date, the fact holds exactly one row per distinct source posting.
with source_rows as (
 select cast(observed_at_utc as date) as observation_date,count(distinct job_key) as source_rows
 from {{ source('facts_input','job_observations') }} group by 1
),fact_rows as (
 select observation_date,count(*) as fact_rows from {{ ref('fact_job_posting_daily') }} group by 1
)
select coalesce(s.observation_date,f.observation_date) as observation_date,
 coalesce(s.source_rows,0) as source_rows,coalesce(f.fact_rows,0) as fact_rows,
 'JOB_OBSERVATION_ACCOUNTING' as problem
from source_rows s full outer join fact_rows f on s.observation_date=f.observation_date
where coalesce(s.source_rows,0)<>coalesce(f.fact_rows,0)
