-- Airbyte deduped current-state table required. Preserve tombstone metadata for audit.
select 'postgres_cdc' as source_system, cast(null as varchar) as source_employer_key,
 'MISSING_EMPLOYER_ID' as employer_identity_status, id as job_id, title as job_title,
 company_name as employer_name, updated_at,
 _ab_cdc_lsn as source_lsn, _ab_cdc_updated_at as source_cdc_updated_at,
 _ab_cdc_deleted_at as source_deleted_at, _airbyte_extracted_at as source_extracted_at,
 '{{ source("jobs", "cdc_job_postings") }}' as source_relation
from {{ source('jobs','cdc_job_postings') }}
where _ab_cdc_deleted_at is null
