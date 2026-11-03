select 'postgres_cdc' as source_system, id as event_id, job_id, event_type, event_at,
 _ab_cdc_lsn as source_lsn, _ab_cdc_updated_at as source_cdc_updated_at,
 _ab_cdc_deleted_at as source_deleted_at, _airbyte_extracted_at as source_extracted_at,
 '{{ source("jobs", "cdc_application_events") }}' as source_relation
from {{ source('jobs','cdc_application_events') }}
where _ab_cdc_deleted_at is null
