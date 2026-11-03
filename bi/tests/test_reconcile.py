"""Meaningful adversarial SQL/evidence tests, explicitly not native engine tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import duckdb

BI=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BI))
from reconcile import load_native


class Reconciliation(unittest.TestCase):
    def setUp(self):
        self.c=duckdb.connect()
        self.c.execute('''CREATE TABLE revisions(case_number VARCHAR,revision INT,is_certified BOOLEAN,annual_wage DOUBLE);
        INSERT INTO revisions VALUES ('A',1,true,999),('A',2,false,10),('B',1,true,20),('C',1,true,40),('D',1,false,NULL);
        CREATE VIEW lca AS SELECT * EXCLUDE(revision) FROM revisions QUALIFY row_number() OVER(PARTITION BY case_number ORDER BY revision DESC)=1;
        CREATE TABLE postings(posting_id VARCHAR,observation_date DATE,is_active BOOLEAN);
        INSERT INTO postings VALUES ('P','2025-01-01',true),('P','2025-01-02',true),('Q','2025-01-02',false);
        CREATE TABLE wages(soc VARCHAR,area VARCHAR,survey_year INT,annual_median_wage DOUBLE);
        INSERT INTO wages VALUES ('15-1252','1:99',2025,50),('15-1252','4:123',2025,60);
        CREATE TABLE cube_results(metric VARCHAR,value DOUBLE);
        CREATE TABLE native_results(engine VARCHAR,metric VARCHAR,value DOUBLE);
        ''')

    def tearDown(self):self.c.close()

    def result(self):
        rows=self.c.execute((BI/'reconcile.sql').read_text()).fetchall()
        return {r[0]:r for r in rows}

    def test_amendment_denominator_null_quantile_and_observed_days(self):
        rows=self.result()
        self.assertEqual({k:r[1] for k,r in rows.items()},{'case_count':4,'certified_rate':.5,'wage_p75':30,'active_posting_days':2,'oews_median':50})
        self.assertTrue(all(r[-1]=='FAIL' for r in rows.values()))

    def test_multiple_or_suppressed_estimates_never_average(self):
        self.c.execute("INSERT INTO wages VALUES ('15-1252','1:99',2025,70)")
        self.assertIsNone(self.result()['oews_median'][1])
        self.c.execute("DELETE FROM wages; INSERT INTO wages VALUES ('15-1252','1:99',2025,NULL)")
        self.assertIsNone(self.result()['oews_median'][1])

    def test_zero_rows_nulls_do_not_stand_in_for_missing_native(self):
        self.c.execute('DELETE FROM revisions; DELETE FROM postings; DELETE FROM wages')
        rows=self.result();self.assertEqual(rows['case_count'][1],0)
        self.assertIsNone(rows['certified_rate'][1]);self.assertIsNone(rows['wage_p75'][1])
        self.assertTrue(all(r[-1]=='FAIL' for r in rows.values()))

    def test_comparison_requires_both_engines_and_catches_wrong_values(self):
        self.result()
        self.c.execute("INSERT INTO cube_results SELECT * FROM sql_oracle; INSERT INTO native_results SELECT 'powerbi_dax',* FROM sql_oracle")
        self.assertTrue(all(r[-1]=='FAIL' for r in self.result().values()))
        self.c.execute("INSERT INTO native_results SELECT 'tableau',* FROM sql_oracle")
        self.assertTrue(all(r[-1]=='PASS' for r in self.result().values()))
        self.c.execute("UPDATE native_results SET value=value+1 WHERE engine='tableau' AND metric='wage_p75'")
        self.assertEqual(self.result()['wage_p75'][-1],'FAIL')

    def test_incomplete_native_attestation_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);(directory/'powerbi_dax.json').write_text(json.dumps({'engine':'powerbi_dax'}))
            with self.assertRaisesRegex(ValueError,'incomplete execution provenance'):
                load_native(self.c,directory,'synthetic')


if __name__=='__main__':unittest.main()
