{{ config(tags=['staging']) }}
with all_files as (
 select source_system,expected_release,source_release,source_sha256,source_url,source_row_number from {{ ref('stg_lca') }}
 union all
 select source_system,expected_release,source_release,source_sha256,source_url,source_row_number from {{ ref('stg_uscis') }}
 union all
 select source_system,expected_release,source_release,source_sha256,source_url,source_row_number from {{ ref('stg_bls') }}
), counted as (
select *,count(*) over(partition by source_system,source_release,source_sha256,source_row_number) as identity_count from all_files
)
select source_system, source_release, source_sha256, source_row_number,
 'MISSING_INVALID_OR_DUPLICATE_PROVENANCE' as error_code
from counted
where source_release is null or source_release != expected_release
 or source_sha256 is null or not {{ matches('source_sha256','[a-f0-9]{64}') }}
 or source_url is null or source_url not like 'https://%'
 or source_row_number is null or source_row_number < 1 or source_row_number != floor(source_row_number)
 or identity_count != 1
