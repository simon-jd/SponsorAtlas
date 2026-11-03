-- psql -v ON_ERROR_STOP=1 -f ingestion/postgres/configure_cdc.sql
-- Execute only on the authorized nonproduction jobs copy, as its owner/admin.
-- Create airbyte_replication LOGIN REPLICATION + set secret separately before this file.
-- Re-running verifies our exact slot/publication instead of replacing other resources.
BEGIN;
DO $$
DECLARE t text;
BEGIN
  IF current_setting('wal_level') <> 'logical' THEN
    RAISE EXCEPTION 'wal_level must be logical; configure PostgreSQL and restart';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airbyte_replication' AND rolreplication) THEN
    RAISE EXCEPTION 'Provision airbyte_replication LOGIN REPLICATION with a secret first';
  END IF;
  FOREACH t IN ARRAY ARRAY['job_postings', 'application_events'] LOOP
    IF NOT EXISTS (
      SELECT FROM pg_constraint c JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
      WHERE c.conrelid = format('public.%I', t)::regclass AND c.contype = 'p'
        AND array_length(c.conkey, 1) = 1 AND a.attname = 'id'
    ) THEN
      RAISE EXCEPTION 'public.% requires a single-column id primary key', t;
    END IF;
  END LOOP;
END $$;
ALTER TABLE public.job_postings REPLICA IDENTITY FULL;
ALTER TABLE public.application_events REPLICA IDENTITY FULL;
GRANT USAGE ON SCHEMA public TO airbyte_replication;
GRANT SELECT ON public.job_postings, public.application_events TO airbyte_replication;
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'sponsoratlas_publication') THEN
    CREATE PUBLICATION sponsoratlas_publication FOR TABLE public.job_postings, public.application_events
      WITH (publish = 'insert, update, delete');
  END IF;
  IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'sponsoratlas_publication'
                 AND pubinsert AND pubupdate AND pubdelete AND NOT puballtables)
    OR (SELECT array_agg(tablename::text ORDER BY tablename)
        FROM pg_publication_tables WHERE pubname = 'sponsoratlas_publication' AND schemaname = 'public')
        IS DISTINCT FROM ARRAY['application_events', 'job_postings']
    OR EXISTS (SELECT FROM pg_publication_tables WHERE pubname = 'sponsoratlas_publication'
               AND schemaname <> 'public') THEN
    RAISE EXCEPTION 'Existing SponsorAtlas publication does not match exact CDC contract';
  END IF;
END $$;
COMMIT;
-- Slot creation must run outside the transaction after any preceding DDL.
SELECT pg_create_logical_replication_slot('sponsoratlas_airbyte', 'pgoutput')
WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = 'sponsoratlas_airbyte');
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = 'sponsoratlas_airbyte'
                 AND plugin = 'pgoutput' AND database = current_database() AND slot_type = 'logical') THEN
    RAISE EXCEPTION 'Existing SponsorAtlas slot is incompatible';
  END IF;
END $$;
SELECT slot_name, plugin, database, active, restart_lsn, confirmed_flush_lsn,
       pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) AS retained_bytes
FROM pg_replication_slots WHERE slot_name = 'sponsoratlas_airbyte';
