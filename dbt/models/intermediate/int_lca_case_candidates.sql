{{ config(materialized='table', schema='INTERMEDIATE', tags=['facts']) }}
-- All occurrences remain accounted for. Release/sha/row tie-breakers are deterministic,
-- not evidence that a same-date source row represents a later business amendment.
select l.*,m.employer_id,m.occupation_key,m.geography_key,
 m.occupation_mapping_status,m.county_mapping_status,
 count(*) over(partition by l.case_number) as source_occurrence_count,
 count(*) over(partition by l.case_number,l.decision_date,l.received_date,l.fiscal_year) as chronological_tie_count,
 row_number() over(partition by l.case_number order by
 l.decision_date desc nulls last,l.received_date desc nulls last,l.fiscal_year desc,
 l.source_release desc,l.source_sha256,l.source_row_number) as winner_rank
from {{ source('facts_input','lca') }} l
left join {{ ref('int_dimension_mapping') }} m
 on l.source_system=m.source_system and l.source_release=m.source_release
 and l.source_sha256=m.source_sha256 and l.source_row_number=m.source_row_number
