"""Small synthetic references + provenance metadata; never opens original archives."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from azure.fabric import plan_bundle
from azure.fabric.archive import OFFICIAL,sha_file
from azure.tests.test_pipeline import synthetic_bundle
from azure.tests.test_port import ROOT

class BundlePlan(unittest.TestCase):
    def test_actual_receipt_is_in_plan_and_missing_artifact_fails(self):
        code=plan_bundle.code_zip()
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            root=Path(directory);manifest=synthetic_bundle(root)
            mapping=root/'dbt/seeds/lca_column_map.csv';mapping.parent.mkdir(parents=True);mapping.write_text('synthetic mapping\n')
            for release,(rows,original,derived) in OFFICIAL.items():
                folder=root/'data/raw/lca'/release;folder.mkdir(parents=True)
                (folder/'synthetic.provenance.json').write_text(json.dumps(dict(path=str(folder/(release+'_'+original+'.jsonl')),release=release,sha256=original,derived_sha256=derived,original_headers=['SYNTHETIC'],source_rows=rows,url='https://www.dol.gov/synthetic-fixture')))
            spec=dict(history_export_receipt_file=str(root/'receipt.json'),er_quality_disclosure='Synthetic fixture; human gate OPEN',references=[dict(table=r['table'],format=r['format'],local_path=str(root/r['path'])) for r in manifest['references']])
            spec_path=root/'spec.json';spec_path.write_text(json.dumps(spec))
            with patch.object(plan_bundle,'ROOT',root),patch.object(plan_bundle,'code_zip',return_value=code):
                result=plan_bundle.prepare(spec_path,root/'plan')
                self.assertEqual(result['kind'],'UNEXECUTED_NATIVE_BUNDLE_PLAN')
                produced=json.loads((root/'plan/manifest.json').read_text())
                copied=root/'plan'/produced['history_export_receipt_path']
                self.assertEqual(sha_file(copied),sha_file(root/'receipt.json'))
                self.assertTrue(any(u['blob_path']==produced['history_export_receipt_path'] for u in json.loads((root/'plan/uploads.json').read_text())))
                self.assertEqual(produced['registry_sha256'],manifest['registry_sha256'])
                (root/'receipt.json').unlink()
                with self.assertRaises(FileNotFoundError):plan_bundle.prepare(spec_path,root/'must-not-exist')
                self.assertFalse((root/'must-not-exist').exists())
