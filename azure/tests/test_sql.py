"""Execute shipped SELECTs with narrow dialect substitutions in existing DuckDB.

This exercises SQL semantics, not Fabric parsing/DDL/driver compatibility.
"""
import re
import hashlib
import unittest
import duckdb
from azure.tests.test_port import envelope, ROOT
from azure.fabric.contract import normalize, annual_map, latest, metrics
from azure.fabric.warehouse import statements,rebuild

SQL = ROOT / 'azure/fabric'


def adapt(text):
    text = text.replace('{{lakehouse}}','lakehouse').replace('COUNT_BIG(', 'COUNT(')
    text = text.replace('datetime2(6)','TIMESTAMP').replace(' AS bit)', ' AS BOOLEAN)')
    text = re.sub(r'DATEADD\(day,1,CAST\(([^)]+) AS TIMESTAMP\)\)',r"(CAST(\1 AS TIMESTAMP)+INTERVAL '1 day')",text)
    text = re.sub(r"LOWER\(CONVERT\(varchar\(32\),HASHBYTES\('MD5',CONCAT\((.*?)\)\),2\)\)",r'md5(CONCAT(\1))',text)
    text = text.replace("DATEADD(day,a.n+10*b.n+100*c.n+1000*d.n+10000*e.n,CAST('2000-01-01' AS date))","(DATE '2000-01-01'+CAST(a.n+10*b.n+100*c.n+1000*d.n+10000*e.n AS INTEGER))")
    text = text.replace('DATEPART(quarter,calendar_date)',"EXTRACT(quarter FROM calendar_date)")
    return text


class SQLBehavior(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect(':memory:',config={'memory_limit':'512MB','threads':1,'max_temp_directory_size':'64MB'})
        self.db.execute("ATTACH ':memory:' AS lakehouse; CREATE SCHEMA lakehouse.dbo; CREATE SCHEMA MARTS")
        ddl = (SQL/'ddl.sql').read_text().replace('CREATE SCHEMA MARTS;','').replace('datetime2(6)','TIMESTAMP')
        ddl = re.sub(r'\bbit\b','BOOLEAN',ddl)
        self.db.execute(ddl)
        row = normalize(envelope(),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025))
        cols=[]
        for k,v in row.items():
            typ = 'VARCHAR'
            if k.endswith('_date'): typ='DATE'
            if k in ('source_row_number','fiscal_year'): typ='BIGINT'
            if k in ('wage_amount','annual_wage'): typ='DECIMAL(24,2)'
            if k=='worker_positions': typ='DECIMAL(38,0)'
            cols.append(k+' '+typ)
        self.columns=list(row)
        self.db.execute('CREATE TABLE lakehouse.dbo.port_lca ('+','.join(cols)+')')
        self.db.execute('CREATE TABLE lakehouse.dbo.port_employer_xref(source_system VARCHAR,source_employer_key VARCHAR,employer_id VARCHAR)')
        self.db.execute('INSERT INTO lakehouse.dbo.port_employer_xref VALUES (?,?,?)',['lca',row['source_employer_key'],'E'])
        self.db.execute("CREATE TABLE lakehouse.dbo.port_county_fips(state_code VARCHAR,county_name VARCHAR,county_fips VARCHAR,geography_vintage VARCHAR)")
        self.db.execute("INSERT INTO lakehouse.dbo.port_county_fips VALUES ('MD','ST MARYS','24037','Census2020')")
        self.db.execute("INSERT INTO MARTS.dim_occupation(occupation_key,soc_code,soc_vintage) VALUES ('O','15-1252','SOC2018')")
        self.db.execute("INSERT INTO MARTS.dim_geography(geography_key,county_fips,county_reference_vintage) VALUES ('G','24037','Census2020')")
        self.db.execute("INSERT INTO MARTS.dim_date(date_key,calendar_date) VALUES (20250101,'2025-01-01'),(20240101,'2024-01-01')")
        self.db.execute("INSERT INTO MARTS.dim_employer(employer_key,employer_id,valid_from,validity_semantics) VALUES ('K','E','2024-01-01','CAPTURE_TIME_UTC')")

    def tearDown(self): self.db.close()

    def insert(self,rows):
        for row in rows:
            self.db.execute('INSERT INTO lakehouse.dbo.port_lca VALUES ('+','.join('?' for _ in self.columns)+')',[row[x] for x in self.columns])

    def query(self):
        cursor = self.db.execute(adapt((SQL/'fact-select.sql').read_text()))
        names=[d[0] for d in cursor.description]
        return [dict(zip(names,row)) for row in cursor.fetchall()]

    def test_real_fact_select_latest_history_fk_and_metrics(self):
        mapping=annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025)
        rows=[normalize(envelope(case=case,wage=wage,decision=day),2025,mapping) for case,wage,day in [('A','10','2024-01-01'),('A','20','2025-01-01'),('B','','2025-01-01'),('C','40','2025-01-01')]]
        for i,row in enumerate(rows): row['source_row_number']=i+2
        rows[2]['case_status']='Certified - Withdrawn';rows[3]['case_status']='Denied'
        self.insert(rows)
        actual=self.query()
        expected=latest(rows)
        self.assertEqual(metrics(actual),metrics(expected))
        self.assertEqual({r['case_number']:r['source_occurrence_count'] for r in actual},{'A':2,'B':1,'C':1})
        self.assertTrue(all(r['employer_key']=='K' and r['geography_key']=='G' and r['occupation_key']=='O' for r in actual))
        self.assertEqual(self.query(),actual)
        self.db.execute('INSERT INTO MARTS.fact_lca_case '+adapt((SQL/'fact-select.sql').read_text()))
        metric=self.db.execute(adapt((SQL/'verify-fabric.sql').read_text())).fetchone()
        self.assertEqual(metric[0],3);self.assertAlmostEqual(float(metric[1]),2/3)
        self.assertEqual(float(metric[2]),62400);self.assertEqual(float(metric[3]),79040)
        for sql in statements((SQL/'checks.sql').read_text()):
            if sql.strip(): self.assertEqual(self.db.execute(adapt(sql)).fetchone()[0],0,sql)

    def test_scd_no_backfill_midday_unknown_and_overlap_check(self):
        row=normalize(envelope(),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025));self.insert([row])
        self.db.execute("UPDATE MARTS.dim_employer SET valid_from='2026-01-01'")
        self.assertEqual(self.query()[0]['employer_history_status'],'PRE_SNAPSHOT_HISTORY_UNKNOWN')
        self.assertIsNone(self.query()[0]['employer_key'])
        self.db.execute("UPDATE MARTS.dim_employer SET valid_from='2025-01-01 12:00:00'")
        self.assertEqual(self.query()[0]['employer_history_status'],'DATE_PRECISION_OR_INTERVAL_UNKNOWN')
        self.db.execute("INSERT INTO MARTS.dim_employer(employer_key,employer_id,valid_from) VALUES ('K2','E','2025-01-01')")
        overlap=[s for s in statements((SQL/'checks.sql').read_text()) if 'a.employer_key<b.employer_key' in s][0]
        self.assertEqual(self.db.execute(adapt(overlap)).fetchone()[0],1)

    def test_fanout_duplicate_source_and_utf8_ddl_contract(self):
        row=normalize(envelope(),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025));self.insert([row,row])
        first=(SQL/'input-checks.sql').read_text().split(';')[0]
        self.assertEqual(self.db.execute(adapt(first)).fetchone()[0],1)
        self.assertEqual(self.query()[0]['source_employer_name'],'Example 雇主')

    def test_actual_sql_empty_null_quantiles_tie_and_missing_dates(self):
        self.assertEqual(self.db.execute(adapt((SQL/'verify-fabric.sql').read_text())).fetchone(),(0,None,None,None))
        row=normalize(envelope(wage='',decision=''),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025))
        tie=dict(row,source_row_number=3,case_status='Denied')
        self.insert([tie,row]);actual=self.query()[0]
        self.assertEqual(actual['case_status'],'Certified')
        self.assertEqual(actual['latest_selection_status'],'SAME_DATE_TIE_REVIEW_REQUIRED')
        self.assertEqual(actual['employer_history_status'],'SOURCE_FACT_DATE_MISSING')
        self.db.execute('INSERT INTO MARTS.fact_lca_case '+adapt((SQL/'fact-select.sql').read_text()))
        self.assertEqual(self.db.execute(adapt((SQL/'verify-fabric.sql').read_text())).fetchone(),(1,1,None,None))

    def dimension_inputs(self):
        for table in ('dim_employer','dim_occupation','dim_geography','dim_date'):self.db.execute('DELETE FROM MARTS.'+table)
        self.db.execute("CREATE TABLE lakehouse.dbo.port_ref_soc_2018(soc_code VARCHAR,soc_title VARCHAR,soc_level VARCHAR,soc_vintage VARCHAR,major_code VARCHAR,minor_code VARCHAR,broad_code VARCHAR,parent_code VARCHAR)")
        self.db.execute("INSERT INTO lakehouse.dbo.port_ref_soc_2018 VALUES ('15-0000','Computer','major','SOC2018','15-0000',NULL,NULL,NULL),('15-1252','Developers 雇主','detailed','SOC2018','15-0000',NULL,NULL,'15-0000')")
        self.db.execute("CREATE TABLE lakehouse.dbo.port_ref_county_2023(county_fips VARCHAR,state_code VARCHAR,county_name VARCHAR,county_reference_vintage VARCHAR,county_internal_latitude VARCHAR,county_internal_longitude VARCHAR)")
        self.db.execute("INSERT INTO lakehouse.dbo.port_ref_county_2023 VALUES ('24037','MD','St Marys','Census2023','38.1234567','-76.1234567'),('09001','CT','Old County','Census2023','41','-72')")
        self.db.execute("INSERT INTO lakehouse.dbo.port_county_fips VALUES ('CT','OLD COUNTY','09001','Census2020')")
        self.db.execute("CREATE TABLE lakehouse.dbo.port_ref_county_cbsa_2023(county_fips VARCHAR,cbsa_code VARCHAR,cbsa_title VARCHAR,cbsa_type VARCHAR)")
        self.db.execute("INSERT INTO lakehouse.dbo.port_ref_county_cbsa_2023 VALUES ('24037','12345','Metro','Metropolitan Statistical Area'),('09001','23456','CT Metro','Metropolitan Statistical Area')")
        self.db.execute("CREATE TABLE lakehouse.dbo.port_employer_history AS SELECT employer_key AS dbt_scd_id,employer_id,employer_name,state,country,city,postcode,attribute_source_system,attribute_source_key,source_fiscal_year,source_observation_period,resolution_status,match_method,identity_quality_status,is_source_active,valid_from AS dbt_valid_from,valid_to AS dbt_valid_to FROM MARTS.dim_employer")
        self.db.execute("INSERT INTO lakehouse.dbo.port_employer_history(dbt_scd_id,employer_id,dbt_valid_from,dbt_valid_to) VALUES ('K1','E','2024-01-01','2025-01-01'),('K2','E','2025-01-01',NULL)")

    def test_actual_dimension_sql_soc_county_capture_history_calendar(self):
        self.dimension_inputs()
        for sql in statements((SQL/'dimensions.sql').read_text()):self.db.execute(adapt(sql))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM MARTS.dim_date').fetchone()[0],13149)
        self.assertEqual(self.db.execute("SELECT fiscal_year,fiscal_quarter FROM MARTS.dim_date WHERE calendar_date='2025-10-01'").fetchone(),(2026,1))
        self.assertEqual(self.db.execute("SELECT MIN(calendar_date)::VARCHAR,MAX(calendar_date)::VARCHAR FROM MARTS.dim_date").fetchone(),('2000-01-01','2035-12-31'))
        self.assertEqual(self.db.execute("SELECT occupation_key,major_title FROM MARTS.dim_occupation WHERE soc_code='15-1252'").fetchone(),(hashlib.md5(b'SOC2018|15-1252').hexdigest(),'Computer'))
        self.assertEqual(self.db.execute("SELECT cbsa_code,cbsa_status FROM MARTS.dim_geography WHERE county_fips='09001' AND county_reference_vintage='Census2020'").fetchone(),(None,'REQUIRES_CT_BRIDGE'))
        self.assertEqual(self.db.execute('SELECT COUNT(*),SUM(CAST(is_current AS INTEGER)) FROM MARTS.dim_employer').fetchone(),(2,1))
        for sql in statements((SQL/'checks.sql').read_text()):self.assertEqual(self.db.execute(adapt(sql)).fetchone()[0],0,sql)
        identity=dict(scope='SYNTHETIC',manifest_sha256='a'*64,model_version='fixture',registry_sha256='b'*64,history_sha256='c'*64)
        self.db.execute('CREATE TABLE lakehouse.dbo.port_identity AS SELECT * FROM MARTS.port_identity')
        self.db.execute('INSERT INTO lakehouse.dbo.port_identity VALUES (?,?,?,?,?)',list(identity.values()))
        row=normalize(envelope(),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025));self.insert([row])
        # The production transactional loader runs unchanged; only the driver
        # execute boundary translates declared dialect constructs for this test.
        class Adapter:
            def __init__(adapter,db):adapter.db=db
            def cursor(adapter):return adapter
            def execute(adapter,sql):return adapter.db.execute(adapt(sql))
            def close(adapter):pass
        bridge=Adapter(self.db)
        rebuild(bridge,'lakehouse',identity)
        first=self.db.execute('SELECT * FROM MARTS.fact_lca_case').fetchall()
        rebuild(bridge,'lakehouse',identity)
        self.assertEqual(self.db.execute('SELECT * FROM MARTS.fact_lca_case').fetchall(),first)
        self.db.execute("INSERT INTO lakehouse.dbo.port_employer_history(dbt_scd_id,employer_id,dbt_valid_from) VALUES ('K3','E','2025-01-01')")
        with self.assertRaisesRegex(ValueError,'checks.sql check'):rebuild(bridge,'lakehouse',identity)
        self.assertEqual(self.db.execute('SELECT * FROM MARTS.fact_lca_case').fetchall(),first)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM MARTS.dim_employer').fetchone()[0],2)
