-- OPTIONAL synthetic acceptance fixture on a dedicated empty, authorized database.
-- Never run against a real job/application database. configure_cdc.sql does not create these.
CREATE TABLE public.job_postings (
  id text PRIMARY KEY,
  title text NOT NULL,
  company_name text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.application_events (
  id text PRIMARY KEY,
  -- Contract v2: no inferred defaults; legacy source requires explicit migration.
  application_id text NOT NULL,
  owner_role text NOT NULL CHECK (owner_role ~ '^SPONSORATLAS_OWNER_[A-Z0-9_]+$'),
  contact_email text,
  contract_version integer NOT NULL DEFAULT 2 CHECK (contract_version = 2),
  job_id text NOT NULL,
  event_type text NOT NULL,
  event_at timestamptz NOT NULL DEFAULT now()
);
