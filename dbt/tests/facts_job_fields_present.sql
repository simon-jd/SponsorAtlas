{{ config(tags=['facts']) }}
-- On the latest observation date every provider has titles and URLs. A renamed provider field reads
-- as NULL and passes every other gate; the board loader refuses such a board first.
select source_provider,observation_date,'JOB_FIELD_MISSING' as problem
from {{ ref('fact_job_posting_daily') }}
where observation_date=(select max(observation_date) from {{ ref('fact_job_posting_daily') }})
group by source_provider,observation_date
having count(nullif(trim(job_title),''))=0 or count(nullif(trim(job_url),''))=0
