"""Focused exporter boundary tests; no upstream dbt suites or actual source mutations."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic.scripts import prepare


class ExportBoundaryTest(unittest.TestCase):
    def test_rejects_fanout_and_duplicate_fact_grain(self):
        original = prepare.fixture
        for target in (
            "dim_occupation",
            "fact_lca_case",
            "fact_job_posting_daily",
            "fact_wage_benchmark",
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:

                def inject(connection, target=target):
                    original(connection)
                    connection.execute(
                        f"insert into ANALYTICS_TEST.{target} select * from ANALYTICS_TEST.{target} limit 1"
                    )

                with (
                    patch.object(prepare, "ROOT", Path(temp)),
                    patch.object(prepare, "fixture", inject),
                    self.assertRaises(AssertionError),
                ):
                    prepare.prepare("fixture")


class SnowflakeOracleTest(unittest.TestCase):
    def test_certified_rate_is_numerator_over_denominator(self):
        from semantic.scripts import export_snowflake

        # Snowflake returns AVG(CASE ... 1.0 ELSE 0.0 END) at NUMBER scale 6 (0.977779);
        # the oracle selects integer counts and divides in Python, so the rate is exact.
        self.assertNotIn("AVG(", export_snowflake.LCA_ORACLE_SQL.upper())
        oracle = export_snowflake.lca_oracle((713467, 697613, 120000, 156383.63, 200000))
        self.assertEqual(oracle["case_count"], 713467)
        self.assertEqual(oracle["certified_count"], 697613)
        self.assertEqual(oracle["certified_rate"], 697613 / 713467)
        self.assertEqual(oracle["certified_rate"], 0.9777789302098064)
        self.assertEqual(oracle["wage_p75"], 156383.63)
        self.assertIsNone(export_snowflake.lca_oracle((0, 0, None, None, None))["certified_rate"])


if __name__ == "__main__":
    unittest.main()
