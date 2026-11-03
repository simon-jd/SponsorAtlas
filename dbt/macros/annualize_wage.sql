{% macro annualize_wage(amount, unit) -%}
(cast({{ amount }} as decimal(18,2)) *
 case {{ unit }}
   when 'Hour' then 2080 when 'Week' then 52 when 'Bi-Weekly' then 26
   when 'Month' then 12 when 'Year' then 1 else null
 end)::decimal(24,2)
{%- endmacro %}
