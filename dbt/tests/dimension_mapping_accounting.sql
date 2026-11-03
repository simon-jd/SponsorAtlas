{{ config(tags=['dimensions']) }}
-- These gates validate coverage and classification, not raw source quality.
select 'ROW_LOSS_OR_DUPLICATION' as problem
where (select count(*) from {{ ref('int_dimension_mapping') }})
   <> (select count(*) from {{ source('dimensions_input','lca_attributes') }})
union all
select 'UNCLASSIFIED_MAPPING' from {{ ref('int_dimension_mapping') }}
where occupation_mapping_status is null or county_mapping_status is null
 or county_mapping_status = 'DIMENSION_COUNTY_MAPPING_FAILED'
 or employer_history_status = 'DIMENSION_EMPLOYER_MAPPING_FAILED'
 or (occupation_mapping_status = 'MATCHED' and occupation_key is null)
 or (employer_id is not null and employer_history_status is null)
union all
select 'DUPLICATE_SOURCE_OCCURRENCE' from {{ ref('int_dimension_mapping') }}
group by source_system,source_release,source_sha256,source_row_number having count(*)<>1
union all
select 'ROW_PROVENANCE_LOSS' from (
 select source_system,source_release,source_sha256,source_row_number
 from {{ source('dimensions_input','lca_attributes') }}
 except
 select source_system,source_release,source_sha256,source_row_number
 from {{ ref('int_dimension_mapping') }}
) lost
union all
select 'UNKNOWN_DIAGNOSTIC_STATUS' from {{ ref('int_dimension_mapping') }}
where occupation_mapping_status not in ('MATCHED','SOURCE_SOC_MISSING','SOURCE_SOC_MALFORMED','SOC_NOT_IN_2018_REFERENCE')
 or county_mapping_status not in ('MATCHED','SOURCE_COUNTY_MISSING','UNRESOLVED_COUNTY')
