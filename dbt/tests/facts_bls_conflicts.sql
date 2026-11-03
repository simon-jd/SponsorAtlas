{{ config(tags=['facts']) }}
select soc_code,area_type,area_code,survey_year,source_row_number,estimate_variants
from {{ ref('int_bls_benchmark_candidates') }} where estimate_variants<>1
