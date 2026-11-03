{% macro file_rows(family, fields) %}
{% set map = ref('lca_column_map') if family == 'lca' else ref('file_column_map') %}
{% for s in raw_sources() if s.family == family %}
select
 '{{ family }}' as source_system, {{ s.year }} as fiscal_year,
 '{{ s.release }}' as expected_release,
 r.source_release, r.source_sha256, r.source_url, r.source_row_number, r.payload_json,
 {% for field in fields %}
 {% set key_query %}
 (select source_column from {{ map }}
  where fiscal_year = {{ s.year }} and disposition = 'mapped'
  and standard_column = '{{ field }}'
  {% if family != 'lca' %}and source_system = '{{ family }}'{% endif %})
 {% endset %}
 nullif({{ json_value('r.payload_json', key_query) }}, '') as {{ field }}{% if not loop.last %},{% endif %}
 {% endfor %}
from {{ source(s.family, s.table) }} r
{% if not loop.last %}union all{% endif %}
{% endfor %}
{% endmacro %}
