{{ config(tags=['staging']) }}
select 'job_postings' as source_table, job_id as source_id, 'INVALID_CDC_METADATA_OR_TIMESTAMP' as error_code
from {{ ref('stg_cdc_job_postings') }}
where updated_at is null or source_cdc_updated_at is null or source_extracted_at is null or source_deleted_at is not null
union all
select 'application_events',event_id,'INVALID_CDC_METADATA_OR_TIMESTAMP'
from {{ ref('stg_cdc_application_events') }}
where event_at is null or source_cdc_updated_at is null or source_extracted_at is null or source_deleted_at is not null
