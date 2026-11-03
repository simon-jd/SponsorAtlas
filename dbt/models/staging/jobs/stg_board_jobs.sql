{% for provider in ['greenhouse','lever','ashby'] %}
select md5(source_provider||':'||source_board) as source_employer_key, job_key, source_provider as source_system, '{{ provider }}' as expected_provider, source_provider, source_board, source_url,
 payload_json, _airbyte_extracted_at as source_extracted_at,
 {% if provider == 'greenhouse' %}
 {{ json_value('payload_json', "'title'") }} as job_title,
 {{ json_value('payload_json', "'company_name'") }} as employer_name,
 {{ json_value(json_value('payload_json', "'location'"), "'name'") }} as location_raw,
 {{ json_value('payload_json', "'absolute_url'") }} as job_url,
 {% elif provider == 'lever' %}
 {{ json_value('payload_json', "'text'") }} as job_title,
 cast(null as varchar) as employer_name,
 {{ json_value(json_value('payload_json', "'categories'"), "'location'") }} as location_raw,
 {{ json_value('payload_json', "'hostedUrl'") }} as job_url,
 {% else %}
 {{ json_value('payload_json', "'title'") }} as job_title,
 cast(null as varchar) as employer_name,
 {{ json_value('payload_json', "'location'") }} as location_raw,
 {{ json_value('payload_json', "'jobUrl'") }} as job_url,
 {% endif %}
 cast(null as varchar) as state_fips, cast(null as varchar) as county_fips,
 'UNRESOLVED_FREE_TEXT' as geography_status
from {{ source('jobs',provider) }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
