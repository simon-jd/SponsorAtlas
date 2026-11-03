{{ config(tags=['staging']) }}
select job_key,source_provider,source_board,'INVALID_BOARD_PROVENANCE' as error_code
from {{ ref('stg_board_jobs') }}
where source_provider is null or source_provider != expected_provider
 or source_board is null or trim(source_board) = ''
 or source_url is null or source_url not like 'https://%'
 or source_extracted_at is null
 or substr(job_key,1,length(source_provider||':'||source_board||':')) != source_provider||':'||source_board||':'
