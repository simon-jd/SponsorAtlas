{{ config(materialized='table', schema='INTERMEDIATE', tags=['dimensions']) }}
-- One diagnostic for every input LCA occurrence. Bad source values remain visible.
select l.*,
 o.occupation_key, g.geography_key, x.employer_id,
 case when l.soc_code_raw is null or trim(l.soc_code_raw) = '' then 'SOURCE_SOC_MISSING'
      when l.soc_code is null then 'SOURCE_SOC_MALFORMED'
      when o.occupation_key is null then 'SOC_NOT_IN_2018_REFERENCE'
      else 'MATCHED' end as occupation_mapping_status,
 case when l.geography_status not in ('MATCHED','MATCHED_FROM_ZIP','MATCHED_FROM_CITY') then l.geography_status
      when g.geography_key is null then 'DIMENSION_COUNTY_MAPPING_FAILED'
      else 'MATCHED' end as county_mapping_status,
 g.cbsa_status,
 case when x.employer_id is null then 'UNRESOLVED_SOURCE_EMPLOYER'
      when first_snapshot.first_valid_from is null then 'DIMENSION_EMPLOYER_MAPPING_FAILED'
      when l.decision_date is null then 'SOURCE_FACT_DATE_MISSING'
      when l.decision_date < first_snapshot.first_valid_from then 'PRE_SNAPSHOT_HISTORY_UNKNOWN'
      else 'CAPTURE_TIME_LOOKUP_REQUIRED' end as employer_history_status
from {{ source('dimensions_input','lca_attributes') }} l
left join {{ ref('dim_occupation') }} o on l.soc_code = o.soc_code and o.soc_vintage = 'SOC2018'
left join {{ ref('dim_geography') }} g
 on l.county_fips = g.county_fips and l.geography_vintage = g.county_reference_vintage
left join {{ source('dimensions_input','employer_sources') }} x
 on l.source_system = x.source_system and l.source_employer_key = x.source_employer_key
left join (select employer_id,min(valid_from) as first_valid_from from {{ ref('dim_employer') }} group by employer_id) first_snapshot
 on x.employer_id = first_snapshot.employer_id
