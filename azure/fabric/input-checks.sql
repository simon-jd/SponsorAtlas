SELECT COUNT_BIG(*) AS violations FROM (SELECT source_system,source_release,source_sha256,source_row_number FROM {{lakehouse}}.dbo.port_lca GROUP BY source_system,source_release,source_sha256,source_row_number HAVING COUNT_BIG(*)<>1) q;
SELECT COUNT_BIG(*) AS violations FROM {{lakehouse}}.dbo.port_lca WHERE case_number IS NULL OR TRIM(case_number)='';
SELECT COUNT_BIG(*) AS violations FROM (SELECT source_system,source_employer_key FROM {{lakehouse}}.dbo.port_employer_xref GROUP BY source_system,source_employer_key HAVING COUNT_BIG(*)<>1 OR source_system IS NULL OR source_employer_key IS NULL) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT state_code,county_name FROM {{lakehouse}}.dbo.port_county_fips GROUP BY state_code,county_name HAVING COUNT_BIG(*)<>1) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT soc_code FROM {{lakehouse}}.dbo.port_ref_soc_2018 GROUP BY soc_code HAVING COUNT_BIG(*)<>1) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT county_fips FROM {{lakehouse}}.dbo.port_ref_county_2023 GROUP BY county_fips HAVING COUNT_BIG(*)<>1) q;
SELECT COUNT_BIG(*) AS violations FROM (SELECT county_fips FROM {{lakehouse}}.dbo.port_ref_county_cbsa_2023 GROUP BY county_fips HAVING COUNT_BIG(*)<>1) q;
