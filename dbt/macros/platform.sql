{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}

{% macro json_value(payload, key) -%}
  {{ return(adapter.dispatch('json_value', 'sponsoratlas')(payload, key)) }}
{%- endmacro %}
{% macro duckdb__json_value(payload, key) -%}
  json_extract_string(cast({{ payload }} as json), '/' || replace(replace({{ key }}, '~', '~0'), '/', '~1'))
{%- endmacro %}
{% macro snowflake__json_value(payload, key) -%}
  get(parse_json({{ payload }}), {{ key }})::varchar
{%- endmacro %}

{# A provider field inside a payload is not guaranteed to be JSON. A bad one reads as NULL here, so
   one malformed row cannot fail the incremental fact on every later run; the board loader refuses
   a board where such rows are common. The outer payload stays strict in json_value. #}
{% macro nested_json_value(payload, key) -%}
  {{ return(adapter.dispatch('nested_json_value', 'sponsoratlas')(payload, key)) }}
{%- endmacro %}
{% macro duckdb__nested_json_value(payload, key) -%}
  json_extract_string(try_cast({{ payload }} as json), '/' || replace(replace({{ key }}, '~', '~0'), '/', '~1'))
{%- endmacro %}
{% macro snowflake__nested_json_value(payload, key) -%}
  get(try_parse_json({{ payload }}), {{ key }})::varchar
{%- endmacro %}

{% macro strict_date(value) -%}
  {{ return(adapter.dispatch('strict_date', 'sponsoratlas')(value)) }}
{%- endmacro %}
{% macro duckdb__strict_date(value) -%}
  cast(try_strptime(nullif(trim({{ value }}), ''),
  ['%Y-%m-%d', '%m/%d/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']) as date)
{%- endmacro %}
{% macro snowflake__strict_date(value) -%}
  coalesce(try_to_date(nullif(trim({{ value }}), ''), 'YYYY-MM-DD'),
           try_to_date(nullif(trim({{ value }}), ''), 'MM/DD/YYYY'),
           try_to_timestamp_ntz({{ value }}, 'YYYY-MM-DD"T"HH24:MI:SS')::date,
           try_to_timestamp_ntz({{ value }}, 'YYYY-MM-DD HH24:MI:SS')::date)
{%- endmacro %}

{% macro number_value(value) -%}
  {{ return(adapter.dispatch('number_value', 'sponsoratlas')(value)) }}
{%- endmacro %}
{% macro duckdb__number_value(value) -%}try_cast({{ value }} as decimal(18,2)){%- endmacro %}
{% macro snowflake__number_value(value) -%}try_to_decimal({{ value }}, 18, 2){%- endmacro %}

{# Validate the original spelling before casting: no fractional digits can round away. #}
{% macro integer_value(value) -%}
 case when {{ matches('trim(' ~ value ~ ')', '[+-]?[0-9]+([.]0+)?') }}
 then {{ adapter.dispatch('integer_cast', 'sponsoratlas')(value) }} else null end
{%- endmacro %}
{% macro duckdb__integer_cast(value) -%}try_cast(trim({{ value }}) as decimal(38,0)){%- endmacro %}
{% macro snowflake__integer_cast(value) -%}try_to_decimal(trim({{ value }}), 38, 0){%- endmacro %}

{% macro matches(value, pattern) -%}
  {{ return(adapter.dispatch('matches', 'sponsoratlas')(value, pattern)) }}
{%- endmacro %}
{% macro duckdb__matches(value, pattern) -%}regexp_full_match({{ value }}, '{{ pattern }}'){%- endmacro %}
{% macro snowflake__matches(value, pattern) -%}regexp_like({{ value }}, '{{ pattern }}'){%- endmacro %}

{% macro normalize_soc(value) -%}
 case
 when {{ matches('trim(' ~ value ~ ')', '[0-9]{2}-[0-9]{4}([.][0-9]{2})?') }} then substr(trim({{ value }}),1,7)
 when {{ matches('trim(' ~ value ~ ')', '[0-9]{6}') }} then substr(trim({{ value }}),1,2)||'-'||substr(trim({{ value }}),3,4)
 else null end
{%- endmacro %}

{% macro raw_schema(name) -%}
 {{ return(name if target.type == 'snowflake' else 'RAW_' ~ name) }}
{%- endmacro %}
{% macro raw_database() -%}{{ return('RAW' if target.type == 'snowflake' else target.database) }}{%- endmacro %}

{% macro raw_sources() %}
 {{ return([
 {'family':'lca','table':'lca_fy2024_q4','release':'FY2024_Q4','year':2024},
 {'family':'lca','table':'lca_fy2025_q1','release':'FY2025_Q1','year':2025},
 {'family':'lca','table':'lca_fy2025_q2','release':'FY2025_Q2','year':2025},
 {'family':'lca','table':'lca_fy2025_q3','release':'FY2025_Q3','year':2025},
 {'family':'lca','table':'lca_fy2025_q4','release':'FY2025_Q4','year':2025},
 {'family':'uscis','table':'uscis_fy2022','release':'FY2022','year':2022},
 {'family':'uscis','table':'uscis_fy2023','release':'FY2023','year':2023},
 {'family':'bls','table':'bls_fy2024','release':'FY2024','year':2024},
 {'family':'bls','table':'bls_fy2025','release':'FY2025','year':2025},
 {'family':'jobs','table':'greenhouse','release':'greenhouse','year':0},
 {'family':'jobs','table':'lever','release':'lever','year':0},
 {'family':'jobs','table':'ashby','release':'ashby','year':0},
 {'family':'jobs','table':'cdc_job_postings','release':'cdc_job_postings','year':0},
 {'family':'jobs','table':'cdc_application_events','release':'cdc_application_events','year':0}
 ]) }}
{% endmacro %}

{% macro county_key(value) -%}
 {{ return(adapter.dispatch('county_key', 'sponsoratlas')(value)) }}
{%- endmacro %}
{% macro duckdb__county_key(value) -%}
 upper(trim(regexp_replace(replace(replace({{ value }}, '.', ''), chr(39), ''), '[[:space:]]+', ' ', 'g')))
{%- endmacro %}
{% macro snowflake__county_key(value) -%}
 upper(trim(regexp_replace(replace(replace({{ value }}, '.', ''), chr(39), ''), '[[:space:]]+', ' ')))
{%- endmacro %}
