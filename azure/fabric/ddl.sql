-- Run once in a dedicated writable Warehouse, never a Lakehouse SQL endpoint.
CREATE SCHEMA MARTS;
CREATE TABLE MARTS.dim_employer (
 employer_key varchar(32) NOT NULL,employer_id varchar(8000) NOT NULL,employer_name varchar(8000),
 state varchar(8000),country varchar(8000),city varchar(8000),postcode varchar(8000),
 attribute_source_system varchar(8000),attribute_source_key varchar(8000),source_fiscal_year int,
 source_observation_period varchar(8000),resolution_status varchar(8000),match_method varchar(8000),
 identity_quality_status varchar(8000),is_source_active bit,valid_from datetime2(6) NOT NULL,
 valid_to datetime2(6),is_current bit,validity_semantics varchar(32)
);
CREATE TABLE MARTS.dim_occupation (
 occupation_key varchar(32) NOT NULL,soc_code varchar(8000),soc_title varchar(8000),soc_level varchar(8000),
 soc_vintage varchar(8000),major_code varchar(8000),major_title varchar(8000),minor_code varchar(8000),
 minor_title varchar(8000),broad_code varchar(8000),broad_title varchar(8000),parent_code varchar(8000),classification_type varchar(32)
);
CREATE TABLE MARTS.dim_geography (
 geography_key varchar(32) NOT NULL,county_fips varchar(8000),state_fips varchar(8000),state_code varchar(8000),
 county_name varchar(8000),county_reference_vintage varchar(8000),cbsa_code varchar(8000),cbsa_title varchar(8000),
 cbsa_type varchar(8000),cbsa_vintage varchar(32),cbsa_status varchar(64),county_internal_latitude decimal(10,7),
 county_internal_longitude decimal(10,7),coordinate_reference_vintage varchar(32),coordinate_semantics varchar(64),
 geography_level varchar(32),area_type varchar(8000),area_code varchar(8000),geography_title varchar(8000),geography_vintage varchar(8000)
);
CREATE TABLE MARTS.dim_date (
 date_key int NOT NULL,calendar_date date NOT NULL,calendar_year int,calendar_month int,day_of_month int,
 calendar_quarter int,fiscal_year int,fiscal_quarter int
);
CREATE TABLE MARTS.fact_lca_case (
 case_number varchar(8000) NOT NULL,case_status varchar(8000),is_certified bit,visa_class varchar(8000),
 job_title varchar(8000),source_employer_name varchar(8000),source_employer_key varchar(32),employer_id varchar(8000),
 employer_key varchar(32),employer_history_status varchar(64),fact_time_precision varchar(16),
 occupation_key varchar(32),geography_key varchar(32),decision_date_key int,decision_date date,received_date date,
 begin_date date,end_date date,soc_code_raw varchar(8000),soc_code varchar(8000),occupation_mapping_status varchar(64),
 county_mapping_status varchar(64),wage_amount decimal(18,2),wage_unit varchar(8000),annual_wage decimal(24,2),
 worker_positions decimal(38,0),source_system varchar(16),source_release varchar(32),source_sha256 varchar(64),
 source_url varchar(8000),source_row_number bigint,fiscal_year int,worksite_state varchar(8000),source_occurrence_count bigint,
 latest_selection_status varchar(64)
);
CREATE TABLE MARTS.port_identity (
 scope varchar(64) NOT NULL,manifest_sha256 varchar(64) NOT NULL,model_version varchar(64) NOT NULL,
 registry_sha256 varchar(64) NOT NULL,history_sha256 varchar(64) NOT NULL
);
-- Constraints are intentionally not relied upon: checks.sql enforces actual data.
-- Fabric permits keys only with NOT ENFORCED; duplicate/FK/interval checks are mandatory.
