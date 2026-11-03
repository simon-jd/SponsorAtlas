-- UTC capture-time intervals are half open. NULL exclusively denotes infinity.
with versions as (select * from {{ ref('dim_employer') }}),
invalid as (
 select employer_id from versions
 where valid_from is null or (valid_to is not null and valid_to <= valid_from)
    or is_current is null or is_current <> (valid_to is null)
 union all
 select employer_id from versions group by employer_id
 having sum(case when is_current then 1 else 0 end) <> 1
 union all
 select employer_id from versions group by employer_id, valid_from having count(*) > 1
 union all
 select a.employer_id from versions a join versions b
   on a.employer_id = b.employer_id and a.valid_from < b.valid_from
  and (a.valid_to is null or b.valid_from < a.valid_to)
  and (b.valid_to is null or a.valid_from < b.valid_to)
)
select * from invalid
