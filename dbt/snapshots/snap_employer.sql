{% snapshot snap_employer %}
{# Terraform has no ANALYTICS.SNAPSHOTS schema yet; Snowflake runs pass snapshot_schema. #}
{{ config(target_schema=var('snapshot_schema', 'SNAPSHOTS'), unique_key='employer_id', strategy='check',
          updated_at=("timezone('UTC', current_timestamp)" if target.type == 'duckdb' else "convert_timezone('UTC', current_timestamp())::timestamp_ntz"),
          check_cols=['employer_name','state','country','city','postcode',
                      'attribute_source_system','attribute_source_key','source_fiscal_year',
                      'source_observation_period','resolution_status','match_method','is_source_active'],
          tags=['dimensions']) }}
-- dbt's real capture timestamp; never backdate to a source fiscal year.
select * from {{ ref('int_employer_current') }}
{% endsnapshot %}
