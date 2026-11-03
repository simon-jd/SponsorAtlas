-- Authorized native Snowflake export ONLY; not run by the local implementation.
-- Documentation projection only: executable sealed templates + receipt exporter
-- are in snowflake-subject.cjs and snowflake.cjs. Pin the parity snapshot.
-- Export the complete rows
-- (not only current rows) as JSONL using an authorized client, hash exact bytes,
-- record schema hash + registry/prediction/model lineage in manifest.json.
SELECT dbt_scd_id,employer_id,employer_name,state,country,city,postcode,
 attribute_source_system,attribute_source_key,source_fiscal_year,source_observation_period,
 resolution_status,match_method,identity_quality_status,is_source_active,
 dbt_valid_from,dbt_valid_to
FROM ANALYTICS.SNAPSHOTS.snap_employer AT (TIMESTAMP => '{snapshot}'::TIMESTAMP_TZ)
ORDER BY employer_id,dbt_valid_from,dbt_scd_id;
