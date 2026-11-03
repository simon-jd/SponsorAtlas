-- Each statement is submitted separately inside one Warehouse transaction.
-- Portable capture history is mandatory: never manufacture it from current xref.
INSERT INTO MARTS.dim_employer
SELECT CAST(dbt_scd_id AS varchar(32)),CAST(employer_id AS varchar(8000)),
 CAST(employer_name AS varchar(8000)),CAST(state AS varchar(8000)),CAST(country AS varchar(8000)),
 CAST(city AS varchar(8000)),CAST(postcode AS varchar(8000)),CAST(attribute_source_system AS varchar(8000)),
 CAST(attribute_source_key AS varchar(8000)),CAST(source_fiscal_year AS int),CAST(source_observation_period AS varchar(8000)),
 CAST(resolution_status AS varchar(8000)),CAST(match_method AS varchar(8000)),CAST(identity_quality_status AS varchar(8000)),
 CAST(is_source_active AS bit),CAST(dbt_valid_from AS datetime2(6)),CAST(dbt_valid_to AS datetime2(6)),
 CAST(CASE WHEN dbt_valid_to IS NULL THEN 1 ELSE 0 END AS bit),'CAPTURE_TIME_UTC'
FROM {{lakehouse}}.dbo.port_employer_history;

INSERT INTO MARTS.dim_occupation
SELECT LOWER(CONVERT(varchar(32),HASHBYTES('MD5',CONCAT(s.soc_vintage,'|',s.soc_code)),2)),
 s.soc_code,s.soc_title,s.soc_level,s.soc_vintage,s.major_code,major.soc_title,
 s.minor_code,minor.soc_title,s.broad_code,broad.soc_title,s.parent_code,'STANDARD_SOC'
FROM {{lakehouse}}.dbo.port_ref_soc_2018 s
LEFT JOIN {{lakehouse}}.dbo.port_ref_soc_2018 major ON s.major_code=major.soc_code
LEFT JOIN {{lakehouse}}.dbo.port_ref_soc_2018 minor ON s.minor_code=minor.soc_code
LEFT JOIN {{lakehouse}}.dbo.port_ref_soc_2018 broad ON s.broad_code=broad.soc_code;

INSERT INTO MARTS.dim_geography
SELECT LOWER(CONVERT(varchar(32),HASHBYTES('MD5',CONCAT(c.county_reference_vintage,'|',c.county_fips)),2)),
 c.county_fips,SUBSTRING(c.county_fips,1,2),c.state_code,c.county_name,c.county_reference_vintage,
 CASE WHEN c.state_code='CT' AND c.county_reference_vintage='Census2020' THEN NULL ELSE b.cbsa_code END,
 CASE WHEN c.state_code='CT' AND c.county_reference_vintage='Census2020' THEN NULL ELSE b.cbsa_title END,
 CASE WHEN c.state_code='CT' AND c.county_reference_vintage='Census2020' THEN NULL ELSE b.cbsa_type END,
 CASE WHEN g.county_fips IS NOT NULL THEN 'CBSA2023-07' END,
 CASE WHEN c.state_code='CT' AND c.county_reference_vintage='Census2020' THEN 'REQUIRES_CT_BRIDGE'
 WHEN g.county_fips IS NULL THEN 'OUTSIDE_2023_REFERENCE'
 WHEN b.cbsa_code IS NULL THEN 'NON_CBSA_2023'
 WHEN c.county_reference_vintage='Census2020' THEN 'CODE_CONTINUITY_TO_2023' ELSE 'MATCHED_2023' END,
 CAST(g.county_internal_latitude AS decimal(10,7)),CAST(g.county_internal_longitude AS decimal(10,7)),
 CASE WHEN g.county_fips IS NOT NULL THEN 'Census2023' END,
 'COUNTY_INTERNAL_POINT_NOT_WORKSITE','COUNTY',NULL,NULL,c.county_name,c.county_reference_vintage
FROM (
 SELECT county_fips,state_code,MIN(county_name) AS county_name,geography_vintage AS county_reference_vintage
 FROM {{lakehouse}}.dbo.port_county_fips GROUP BY county_fips,state_code,geography_vintage
 UNION ALL
 SELECT county_fips,state_code,county_name,county_reference_vintage FROM {{lakehouse}}.dbo.port_ref_county_2023
) c
LEFT JOIN {{lakehouse}}.dbo.port_ref_county_2023 g ON c.county_fips=g.county_fips
LEFT JOIN {{lakehouse}}.dbo.port_ref_county_cbsa_2023 b ON c.county_fips=b.county_fips;

INSERT INTO MARTS.dim_date
SELECT YEAR(calendar_date)*10000+MONTH(calendar_date)*100+DAY(calendar_date),calendar_date,
 YEAR(calendar_date),MONTH(calendar_date),DAY(calendar_date),DATEPART(quarter,calendar_date),
 YEAR(calendar_date)+CASE WHEN MONTH(calendar_date)>=10 THEN 1 ELSE 0 END,
 CASE WHEN MONTH(calendar_date)>=10 THEN 1 WHEN MONTH(calendar_date)>=7 THEN 4 WHEN MONTH(calendar_date)>=4 THEN 3 ELSE 2 END
FROM (
 SELECT DATEADD(day,a.n+10*b.n+100*c.n+1000*d.n+10000*e.n,CAST('2000-01-01' AS date)) AS calendar_date
 FROM (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) a(n)
 CROSS JOIN (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) b(n)
 CROSS JOIN (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) c(n)
 CROSS JOIN (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) d(n)
 CROSS JOIN (VALUES(0),(1)) e(n)
) dates WHERE calendar_date<CAST('2036-01-01' AS date);
