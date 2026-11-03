{% macro private_application_ddl(relation, query, replacing=false) %}
create {% if replacing %}or replace {% endif %}table {{ relation }} (
    application_id varchar WITH TAG (ANALYTICS.MARTS.SENSITIVITY = 'restricted'),
    owner_role varchar WITH TAG (ANALYTICS.MARTS.SENSITIVITY = 'restricted'),
    job_id varchar,
    event_id varchar,
    application_status varchar,
    last_event_at timestamp_ntz,
    contact_email varchar WITH MASKING POLICY ANALYTICS.MARTS.CONTACT_EMAIL_V1
        WITH TAG (ANALYTICS.MARTS.PII = 'true', ANALYTICS.MARTS.SENSITIVITY = 'restricted'),
    source_lsn number(38,0),
    source_cdc_updated_at timestamp_ntz,
    source_extracted_at timestamp_ntz
)
{% if replacing %}COPY GRANTS{% endif %}
WITH ROW ACCESS POLICY ANALYTICS.MARTS.APPLICATION_OWNER_V1 ON (owner_role)
as ({{ query }})
{% endmacro %}

{% materialization private_applications, adapter='snowflake' %}
  {# Single atomic DDL: no data-bearing tmp, backup, clone, or unbound post-hook. #}
  {% if config.get('pre_hook') or config.get('post_hook') %}
    {{ exceptions.raise_compiler_error('Private application materialization forbids hooks') }}
  {% endif %}
  {% set existing = load_cached_relation(this) %}
  {% if existing and not existing.is_table %}
    {{ exceptions.raise_compiler_error('Refuse non-table replacement; migrate explicitly') }}
  {% endif %}
  {% call statement('main') %}
    {{ private_application_ddl(this, sql, existing is not none) }}
  {% endcall %}
  {{ return({'relations': [this]}) }}
{% endmaterialization %}

{% materialization private_applications, adapter='duckdb' %}
  {# Local contract validation ONLY. DuckDB cannot enforce Snowflake RAP or masks. #}
  {% call statement('main') %}
    create or replace table {{ this }} as ({{ sql }})
  {% endcall %}
  {{ adapter.commit() }}
  {{ return({'relations': [this]}) }}
{% endmaterialization %}

{% macro approved_application_roles_sql() %}
  {% set roles = var('approved_application_owner_roles', []) %}
  {% for role in roles %}
    {% if role is not string or not modules.re.fullmatch('SPONSORATLAS_OWNER_[A-Z0-9_]+', role) %}
      {{ exceptions.raise_compiler_error('Invalid approved application owner role') }}
    {% endif %}
  {% endfor %}
  {{ return("'" ~ roles | join("','") ~ "'") }}
{% endmacro %}
