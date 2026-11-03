{{ config(enabled=var('enable_private_applications', false), tags=['private_applications']) }}
with events as (select * from {{ ref('stg_private_application_events') }}),
invalid as (
  select event_id from events
  where contract_version is null or contract_version <> 2
     or application_id is null or trim(application_id) = ''
     or owner_role is null or not {{ matches('owner_role', 'SPONSORATLAS_OWNER_[A-Z0-9_]+') }}
     or owner_role not in ({{ approved_application_roles_sql() }})
     or event_id is null or job_id is null or event_at is null or source_lsn is null
     or event_type is null or event_type not in ('submitted','interview','offer','rejected','withdrawn','application_deleted','insert','update')
  union all
  select min(event_id) from events group by event_id having count(*) > 1
  union all
  select min(event_id) from events group by application_id
  having count(distinct owner_role) <> 1 or count(distinct job_id) <> 1
)
select * from invalid
