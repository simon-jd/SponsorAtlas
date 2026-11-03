{{ config(tags=['facts']) }}
select 'LCA_INPUT_MAPPING_LOSS' as problem
where (select count(*) from {{ source('facts_input','lca') }})
 <> (select count(*) from {{ ref('int_lca_case_candidates') }})
union all
select 'LCA_WINNER_COUNT' where
 (select count(distinct case_number) from {{ ref('int_lca_case_candidates') }} where nullif(trim(case_number),'') is not null)
 <> (select count(*) from {{ ref('fact_lca_case') }})
union all
select 'WAGE_CANONICAL_COUNT' where
 (select count(*) from {{ ref('int_bls_benchmark_candidates') }} where canonical_rank=1 and estimate_variants=1)
 <> (select count(*) from {{ ref('fact_wage_benchmark') }})
union all
select 'LCA_INVALID_CASE_NUMBER' from {{ ref('int_lca_case_candidates') }} where nullif(trim(case_number),'') is null
union all
select 'LCA_CANONICAL_EMPLOYER_MISSING_FROM_DIMENSION' from {{ ref('fact_lca_case') }} f
where f.employer_id is not null and not exists (select 1 from {{ ref('dim_employer') }} d where d.employer_id=f.employer_id)
