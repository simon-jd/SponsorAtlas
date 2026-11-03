{{ config(enabled=var('enable_private_applications', false), materialized='private_applications', schema='MARTS', tags=['private_applications']) }}
with current_events as (
    select application_id, owner_role, job_id, event_id, event_type, event_at,
           contact_email, source_lsn, source_cdc_updated_at, source_extracted_at,
           row_number() over (
        partition by application_id order by event_at desc, source_lsn desc, event_id desc
    ) as application_rank
    from {{ ref('stg_private_application_events') }}
    where source_deleted_at is null
)
select application_id, owner_role, job_id, event_id, event_type as application_status,
       event_at as last_event_at, contact_email, source_lsn, source_cdc_updated_at,
       source_extracted_at
from current_events
where application_rank = 1 and event_type <> 'application_deleted'
