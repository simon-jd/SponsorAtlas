{% macro payload_keys(relation) %}{{ return(adapter.dispatch('payload_keys', 'sponsoratlas')(relation)) }}{% endmacro %}
{% macro duckdb__payload_keys(relation) %}
 select distinct unnest(json_keys(cast(payload_json as json))) as source_column
 from {{ relation }}
{% endmacro %}
{% macro snowflake__payload_keys(relation) %}
 select distinct k.key::varchar as source_column from {{ relation }} r,
 lateral flatten(input => parse_json(r.payload_json)) k
{% endmacro %}
