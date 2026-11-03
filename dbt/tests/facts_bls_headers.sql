{{ config(tags=['facts']) }}
-- Unknown fields fail instead of silently disappearing from the annual map.
select k.source_column from ({{ payload_keys(ref('stg_bls')) }}) k
left join {{ ref('file_column_map') }} m on m.source_system='bls' and m.fiscal_year=2025
 and k.source_column=m.source_column
where m.source_column is null
