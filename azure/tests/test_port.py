"""Synthetic contracts, never evidence of native Snowflake/Fabric execution."""
import copy
import json
import math
import unittest
from decimal import Decimal
from pathlib import Path

from azure.fabric.contract import annual_map, normalize, latest, attach_history, metrics, compare

ROOT = Path(__file__).resolve().parents[2]
# Scratch parent for the synthetic bundles some tests build inside the repository.
(ROOT / 'azure/fabric/evidence').mkdir(exist_ok=True)
SHA = 'a' * 64


def envelope(case='A', wage='10.005', unit='Hour', decision='2025-01-01', **extra):
    mapping = annual_map(ROOT / 'dbt/seeds/lca_column_map.csv', 2025)
    payload = {col: '' for col in mapping.values()}
    payload.update(CASE_NUMBER=case, CASE_STATUS='Certified', WAGE_RATE_OF_PAY_FROM=wage,
                   WAGE_UNIT_OF_PAY=unit, DECISION_DATE=decision, RECEIVED_DATE='2024-12-01',
                   EMPLOYER_NAME='Example 雇主', EMPLOYER_COUNTRY='UNITED STATES OF AMERICA',
                   SOC_CODE='151252', WORKSITE_COUNTY="St. Mary's", WORKSITE_STATE=' md ',
                   TOTAL_WORKER_POSITIONS='2.0')
    payload.update(extra)
    return dict(source_release='FY2025_Q4', source_sha256=SHA,
                source_url='https://www.dol.gov/example.xlsx', source_row_number=2,
                payload_json=json.dumps(payload))


class Normalization(unittest.TestCase):
    def row(self, **kw):
        return normalize(envelope(**kw), 2025, annual_map(ROOT / 'dbt/seeds/lca_column_map.csv', 2025))

    def test_annual_mapping_and_precision(self):
        row = self.row()
        self.assertEqual(row['annual_wage'], Decimal('20820.80'))
        self.assertEqual(row['worker_positions'], Decimal('2'))
        self.assertEqual(row['soc_code'], '15-1252')
        self.assertEqual(row['worksite_county'], 'ST MARYS')
        self.assertEqual(row['worksite_state'], 'MD')
        self.assertEqual(len(row['source_employer_key']), 32)
        for unit, factor in [('Hour',2080),('Week',52),('Bi-Weekly',26),('Month',12),('Year',1)]:
            self.assertEqual(self.row(wage='10', unit=unit)['annual_wage'], Decimal(10*factor))

    def test_nulls_invalid_values_dates_and_overflow(self):
        for wage in ['', 'NaN', 'Infinity', '1e50', 'abc']:
            self.assertIsNone(self.row(wage=wage)['annual_wage'])
        self.assertIsNone(self.row(unit='Daily')['annual_wage'])
        self.assertIsNone(self.row(decision='2025-02-30')['decision_date'])
        self.assertIsNone(self.row(TOTAL_WORKER_POSITIONS='2.1')['worker_positions'])
        self.assertEqual(self.row(decision='01/31/2025')['decision_date'], '2025-01-31')

    def test_annual_schema_drift_rejected(self):
        row = envelope()
        row['payload_json'] = '{}'
        with self.assertRaisesRegex(ValueError, 'schema'):
            normalize(row, 2025, annual_map(ROOT / 'dbt/seeds/lca_column_map.csv', 2025))
        with self.assertRaises(ValueError):
            annual_map(ROOT / 'dbt/seeds/lca_column_map.csv', 2023)

    def test_latest_retains_late_old_amendments_and_ties(self):
        old = self.row(decision='2024-01-01')
        new = self.row(decision='2025-01-01')
        old['source_row_number'] = 3
        tied = dict(new, source_row_number=4, case_status='Denied')
        result = latest([new, old, tied])[0]
        self.assertEqual(result['case_status'], 'Certified')
        self.assertEqual(result['source_occurrence_count'], 3)
        self.assertEqual(result['latest_selection_status'], 'SAME_DATE_TIE_REVIEW_REQUIRED')
        self.assertEqual(latest([tied, new, old]), latest([new, old, tied]))
        with self.assertRaisesRegex(ValueError, 'occurrence'):
            latest([new, new])
        with self.assertRaisesRegex(ValueError, 'case'):
            latest([dict(new, case_number='')])

    def test_history_whole_day_null_and_precision(self):
        row = dict(self.row(), employer_id='E')
        version = dict(employer_id='E', employer_key='K', valid_from='2024-01-01T00:00:00', valid_to=None)
        self.assertEqual(attach_history(row, [version])['employer_key'], 'K')
        future = dict(version, valid_from='2026-01-01T00:00:00')
        out = attach_history(row, [future])
        self.assertIsNone(out['employer_key'])
        self.assertEqual(out['employer_history_status'], 'PRE_SNAPSHOT_HISTORY_UNKNOWN')
        noon = dict(version, valid_from='2025-01-01T12:00:00')
        self.assertEqual(attach_history(row, [noon])['employer_history_status'], 'DATE_PRECISION_OR_INTERVAL_UNKNOWN')
        self.assertIsNone(attach_history(dict(row, employer_id=None), [version])['employer_key'])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            attach_history(row, [version, dict(version, employer_key='K2')])


class Comparison(unittest.TestCase):
    def test_exact_continuous_and_denominator(self):
        rows = [dict(case_number=str(i), case_status=s, annual_wage=w) for i,(s,w) in enumerate([
            ('Certified',Decimal(10)),('Certified - Withdrawn',Decimal(20)),('Denied',Decimal(40)),('Withdrawn',None)])]
        self.assertEqual(metrics(rows), dict(case_count=4,certified_rate=Decimal('.5'),wage_p50=Decimal(20),wage_p90=Decimal(36)))
        self.assertEqual(metrics([]), dict(case_count=0, certified_rate=None,wage_p50=None,wage_p90=None))
        self.assertIsNone(metrics([dict(case_number='A',case_status='Denied',annual_wage=None)])['wage_p50'])

    def test_comparator_identity_null_nonfinite_exactcount(self):
        identity = dict(scope='LCA_FY2024_Q4+FY2025_Q4', manifest_sha256=SHA, model_version='v1', registry_sha256=SHA, history_sha256=SHA)
        a = dict(identity=identity, metrics=dict(case_count=1,certified_rate=1,wage_p50=10,wage_p90=10))
        compare(a, copy.deepcopy(a))
        for field,value in [('case_count',1.0),('case_count',2),('certified_rate',None),('wage_p50',math.inf),('wage_p90',10.1)]:
            b = copy.deepcopy(a); b['metrics'][field] = value
            with self.assertRaises(ValueError): compare(a,b)
        for field in identity:
            b = copy.deepcopy(a); b['identity'][field] = 'different'
            with self.assertRaises(ValueError): compare(a,b)
        with self.assertRaises(ValueError): compare(a,a,abs_tol=float('nan'))

    def test_config_fails_before_optional_driver_import(self):
        from azure.fabric.native import validate_config
        with self.assertRaisesRegex(ValueError, 'configuration'):
            validate_config({}, {})


if __name__ == '__main__':
    unittest.main()
