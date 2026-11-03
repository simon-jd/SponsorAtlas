-- Selection dependencies: {{ ref('stg_lca') }} {{ ref('stg_uscis') }} {{ ref('stg_bls') }}
-- {{ ref('stg_board_jobs') }} {{ ref('stg_cdc_job_postings') }} {{ ref('stg_cdc_application_events') }}
{{ config(tags=['staging']) }}
with expected_payload as (
 select 'lca' as source_system, fiscal_year, '' as release_key, source_column from {{ ref('lca_column_map') }}
 union all
 select source_system, fiscal_year, '' as release_key, source_column from {{ ref('file_column_map') }}
 union all
 select 'jobs', 0, release_key, source_column from {{ ref('board_column_map') }}
), observed as (
{% for s in raw_sources() %}
 select '{{ s.family }}' as source_system, '{{ s.table }}' as source_table,
 {{ s.year }} as fiscal_year, '{{ s.release }}' as release_key,
 'PHYSICAL' as column_kind, column_name as source_column
 from {% if target.type == 'snowflake' %}{{ adapter.quote(source(s.family,s.table).database) }}.{% endif %}information_schema.columns
 where upper(table_schema) = upper('{{ source(s.family,s.table).schema }}')
 and upper(table_name) = upper('{{ source(s.family,s.table).identifier }}')
 {% if not s.table.startswith('cdc_') %}
 union all
 select '{{ s.family }}', '{{ s.table }}', {{ s.year }}, '{{ s.release }}', 'PAYLOAD', source_column
 from ({{ payload_keys(source(s.family,s.table)) }}) k
 {% endif %}
 {% if not loop.last %}union all{% endif %}
{% endfor %}
)
select o.* from observed o
left join {{ ref('physical_column_map') }} p
 on o.column_kind = 'PHYSICAL' and p.source_system = o.source_system
 and p.release_key = case when o.source_system = 'jobs' then o.release_key else 'FILE' end
 and upper(p.source_column) = upper(o.source_column)
left join expected_payload j
 on o.column_kind = 'PAYLOAD' and o.source_system = j.source_system
 and o.fiscal_year = j.fiscal_year and o.source_column = j.source_column
 and (o.source_system != 'jobs' or o.release_key = j.release_key)
where (o.column_kind = 'PHYSICAL' and p.source_column is null)
 or (o.column_kind = 'PAYLOAD' and j.source_column is null)
