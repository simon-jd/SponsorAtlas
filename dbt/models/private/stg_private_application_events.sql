{{ config(enabled=var('enable_private_applications', false), materialized='table', schema='STAGING', tags=['private_applications']) }}
-- Contract v2 is an explicit opt-in. Older Task3 sources remain usable without these fields.
select id as event_id, application_id, owner_role, job_id, event_type,
       cast(event_at as timestamp) as event_at, contact_email, contract_version,
       cast(_ab_cdc_lsn as decimal(38,0)) as source_lsn,
       cast(_ab_cdc_updated_at as timestamp) as source_cdc_updated_at,
       cast(_ab_cdc_deleted_at as timestamp) as source_deleted_at,
       cast(_airbyte_extracted_at as timestamp) as source_extracted_at
from {{ source('jobs', 'cdc_application_events') }}
