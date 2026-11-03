{{ config(materialized='table', schema='MARTS', tags=['dimensions']) }}
-- Explicit bounded calendar; Oct 1 starts the next federal fiscal year.
with dates as (
 {% if target.type == 'duckdb' %}
 select cast(range as date) as calendar_date
 from range(date '2000-01-01', date '2036-01-01', interval '1 day')
 {% else %}
 select dateadd(day, row_number() over(order by seq4()) - 1, date '2000-01-01')::date as calendar_date
 from table(generator(rowcount => 13149))
 {% endif %}
)
select cast(extract(year from calendar_date)*10000 + extract(month from calendar_date)*100 + extract(day from calendar_date) as integer) as date_key,
 calendar_date, cast(extract(year from calendar_date) as integer) as calendar_year,
 cast(extract(month from calendar_date) as integer) as calendar_month,
 cast(extract(day from calendar_date) as integer) as day_of_month,
 cast(extract(quarter from calendar_date) as integer) as calendar_quarter,
 cast(extract(year from calendar_date) + case when extract(month from calendar_date)>=10 then 1 else 0 end as integer) as fiscal_year,
 cast(case when extract(month from calendar_date)>=10 then 1
           when extract(month from calendar_date)>=7 then 4
           when extract(month from calendar_date)>=4 then 3 else 2 end as integer) as fiscal_quarter
from dates
