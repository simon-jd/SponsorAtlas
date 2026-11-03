{{ config(tags=['facts']) }}
select 'JOB_DAILY_DUPLICATE' as problem from {{ ref('fact_job_posting_daily') }}
group by posting_id,observation_date having count(*)<>1
union all
select 'WAGE_BENCHMARK_DUPLICATE' from {{ ref('fact_wage_benchmark') }}
group by soc_code,area_type,area_code,survey_year having count(*)<>1
