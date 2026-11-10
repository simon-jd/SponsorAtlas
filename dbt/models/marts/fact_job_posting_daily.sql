{{ config(materialized='incremental', incremental_strategy='delete+insert',
 unique_key='observation_date',schema='MARTS',on_schema_change='fail',tags=['facts']) }}
-- delete+insert replaces whole observation dates, so a refetch that drops a posting leaves no stale
-- row. The grain stays one row per posting and date (qualify below, tests/facts_grain.sql).
-- The one-day lookback bounds each run: the loader only ever fetches today, so older dates stay put.
with observations as (
 select *,cast(observed_at_utc as date) as observation_date
 from {{ source('facts_input','job_observations') }}
 {% if is_incremental() %}
 where cast(observed_at_utc as date)>=coalesce((select {{ dbt.dateadd('day','-1','max(observation_date)') }} from {{ this }}),cast('1900-01-01' as date))
 {% endif %}
 qualify row_number() over(partition by job_key,cast(observed_at_utc as date)
 order by observed_at_utc desc,source_sha256 desc)=1
)
select j.job_key as posting_id,j.job_key,j.source_provider,j.source_board,j.observation_date,
 d.date_key as observation_date_key,j.observed_at_utc,j.source_url,j.source_sha256,j.payload_json,
 case when j.source_provider='lever' then {{ json_value('j.payload_json',"'text'") }}
 else {{ json_value('j.payload_json',"'title'") }} end as job_title,
 case when j.source_provider='greenhouse' then {{ json_value('j.payload_json',"'company_name'") }}
 when j.source_provider='postgres_cdc' then {{ json_value('j.payload_json',"'company_name'") }} end as source_employer_name,
 case when j.source_provider='greenhouse' then {{ nested_json_value(json_value('j.payload_json',"'location'"),"'name'") }}
 when j.source_provider='lever' then {{ nested_json_value(json_value('j.payload_json',"'categories'"),"'location'") }}
 else {{ json_value('j.payload_json',"'location'") }} end as location_raw,
 case when j.source_provider='ashby' then {{ json_value('j.payload_json',"'jobUrl'") }}
 when j.source_provider='lever' then {{ json_value('j.payload_json',"'hostedUrl'") }}
 else {{ json_value('j.payload_json',"'absolute_url'") }} end as job_url,
 cast(null as varchar) as employer_id,cast(null as varchar) as employer_key,
 cast(null as varchar) as occupation_key,cast(null as varchar) as geography_key,
 'UNRESOLVED_SOURCE_EMPLOYER' as employer_mapping_status,'UNRESOLVED_FREE_TEXT' as occupation_mapping_status,
 'UNRESOLVED_FREE_TEXT' as geography_mapping_status,
 case when j.source_provider='ashby' then try_cast({{ json_value('j.payload_json',"'isListed'") }} as boolean) end as is_listed,
 j.source_deleted_at is null and coalesce({{ json_value('j.payload_json',"'isListed'") }},'true')<>'false' as is_active,
 j.source_deleted_at,j.replay_kind
from observations j left join {{ ref('dim_date') }} d on j.observation_date=d.calendar_date
