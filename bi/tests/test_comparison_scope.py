"""Null/ambiguous benchmarks and geographic boundaries for the SQL reference."""
import sys
import unittest
from pathlib import Path
import duckdb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from verify_comparison import reference

class ComparisonScope(unittest.TestCase):
    def setUp(self):
        self.con=duckdb.connect(config={'threads':1,'memory_limit':'128MB'})
        self.con.execute("CREATE TABLE lca AS SELECT * FROM (VALUES ('A','35620',2025,100.0),('A','35620',2025,200.0),('A','41860',2024,1000.0),('B','35620',2025,50.0)) t(soc,metro,fiscal_year,annual_wage)")
        self.con.execute("CREATE TABLE wages AS SELECT * FROM (VALUES ('A','1:99',2025,150.0),('A','4:35620',2025,125.0),('B','4:35620',2025,NULL)) t(soc,area,survey_year,annual_median_wage)")
    def tearDown(self):self.con.close()
    def test_national_cbsa_fy_and_soc_are_shared(self):
        national=reference(self.con,'A','1:99',0,2025)
        metro=reference(self.con,'A','4:35620',2025,2025)
        self.assertEqual((national['p75'],metro['p75'],metro['ratio_p75']),(600,175,1.4))
        self.assertEqual(reference(self.con,'A','1:99',2025,2025)['oews_median'],150)
        self.assertEqual(reference(self.con,'B','4:35620',0,2025)['p75'],50)
    def test_unsupported_unknown_suppressed_multiple_and_empty(self):
        for area in ('2:06','UNKNOWN','4:UNKNOWN','5:35620'):
            row=reference(self.con,'A',area,0,2025)
            self.assertEqual(row['status'],'UNSUPPORTED_AREA')
            self.assertIsNone(row['ratio_p75'])
        self.assertIsNone(reference(self.con,'A','4:00000',0,2025)['p75'])
        self.assertEqual(reference(self.con,'B','4:35620',0,2025)['status'],'SUPPRESSED_OR_TOP_CODED')
        self.con.execute("INSERT INTO wages VALUES ('A','4:35620',2025,999)")
        multiple=reference(self.con,'A','4:35620',0,2025)
        self.assertEqual(multiple['status'],'MULTIPLE_ESTIMATES')
        self.assertIsNone(multiple['oews_median'])
        self.assertIsNone(multiple['ratio_p75'])

if __name__=='__main__':unittest.main()
