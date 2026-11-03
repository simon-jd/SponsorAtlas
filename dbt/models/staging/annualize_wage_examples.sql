-- Executable dbt SQL-model unit-test harness for the public macro interface.
select amount, unit, {{ annualize_wage('amount', 'unit') }} as annual_wage
from {{ ref('wage_test_inputs') }}
