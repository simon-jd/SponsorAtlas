"""Focused I1/I2 regression proofs: synthetic SQL, never native acceptance."""
import copy
import unittest
from pathlib import Path
import tempfile
from azure.tests import test_sql
from azure.tests.test_port import envelope,ROOT
from azure.fabric.contract import normalize,annual_map,compare
from azure.fabric.warehouse import rebuild,TABLES
from azure.fabric import native
from azure.fabric.archive import verify_bundle
from azure.tests.test_pipeline import synthetic_bundle


class Bridge:
    def __init__(self,db):self.db=db
    def cursor(self):return self
    def execute(self,sql):return self.db.execute(test_sql.adapt(sql))
    def close(self):pass


class AtomicAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture=test_sql.SQLBehavior();self.fixture.setUp();self.fixture.dimension_inputs()
        self.db=self.fixture.db;self.bridge=Bridge(self.db)
        self.identity=dict(scope='SYNTHETIC',manifest_sha256='a'*64,model_version='fixture',registry_sha256='b'*64,history_sha256='c'*64)
        self.db.execute('CREATE TABLE lakehouse.dbo.port_identity AS SELECT * FROM MARTS.port_identity')
        self.db.execute('INSERT INTO lakehouse.dbo.port_identity VALUES (?,?,?,?,?)',list(self.identity.values()))
        row=normalize(envelope(),2025,annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025));self.fixture.insert([row])
        rebuild(self.bridge,'lakehouse',self.identity)

    def tearDown(self):self.fixture.tearDown()

    def snapshot(self):
        return {table:self.db.execute('SELECT * FROM MARTS.'+table+' ORDER BY 1').fetchall() for table in TABLES}

    def measurements(self,cursor):
        values=cursor.execute((test_sql.SQL/'verify-fabric.sql').read_text()).fetchone()
        return dict(identity=self.identity,metrics=dict(zip(('case_count','certified_rate','wage_p50','wage_p90'),values)))

    def test_metric_witness_and_target_query_failure_preserve_previous_subject(self):
        previous=self.snapshot();reference=self.measurements(self.bridge)
        self.db.execute("UPDATE lakehouse.dbo.port_lca SET annual_wage=annual_wage+100,job_title='Candidate title'")
        self.db.execute("UPDATE lakehouse.dbo.port_employer_history SET employer_name='Candidate name'")
        new_identity=dict(self.identity,manifest_sha256='d'*64)
        self.db.execute("UPDATE lakehouse.dbo.port_identity SET manifest_sha256=?",['d'*64])
        for failure in ('metric','witness','query'):
            def acceptance(cursor):
                actual=self.measurements(cursor)
                if failure=='metric':compare(reference,actual)
                elif failure=='witness':
                    observed=cursor.execute('SELECT job_title FROM MARTS.fact_lca_case').fetchone()[0]
                    if observed=='Candidate title':raise ValueError('actual witness mismatch')
                else:cursor.execute('SELECT missing_column FROM MARTS.fact_lca_case')
            with self.subTest(failure=failure):
                with self.assertRaises(Exception):
                    rebuild(self.bridge,'lakehouse',new_identity,accept=acceptance)
                self.assertEqual(self.snapshot(),previous)

    def test_native_acceptance_full_content_runs_before_commit(self):
        from unittest.mock import patch
        from azure.fabric.content import SUBJECT,columns,content_digest
        def measure(cursor,config):
            result=self.measurements(cursor)
            result['content']={table:content_digest(table,cursor.execute('SELECT '+','.join(columns(table))+' FROM MARTS.'+table).fetchall()) for table in SUBJECT}
            return result
        reference=measure(self.bridge,{})
        before=self.snapshot()
        self.db.execute("UPDATE lakehouse.dbo.port_employer_history SET postcode='Changed unused attribute'")
        with patch.object(native,'fabric_receipt',side_effect=measure):
            with self.assertRaisesRegex(ValueError,'full subject'):
                native.verify_or_load(self.bridge,dict(lakehouse='lakehouse',identity=self.identity),reference,None,load=True)
        self.assertEqual(before,self.snapshot())


class SourceIdentity(unittest.TestCase):
    def test_employer_change_with_same_old_witness_is_rejected(self):
        old_fields=('case_number','source_release','source_sha256','source_row_number','fiscal_year','employer_key','employer_history_status','source_occurrence_count')
        row=dict(case_number='A',employer_id='E1',employer_key=None,employer_history_status='PRE_SNAPSHOT_HISTORY_UNKNOWN')
        changed=dict(row,employer_id='E2')
        self.assertEqual([row.get(k) for k in old_fields],[changed.get(k) for k in old_fields])
        self.assertNotEqual(native.witness_digest([[row.get(k) for k in native.WITNESS]]),native.witness_digest([[changed.get(k) for k in native.WITNESS]]))

    def test_receipt_must_be_an_actual_bound_artifact(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            root=Path(directory);manifest=synthetic_bundle(root)
            manifest['history_export_receipt_path']='missing.json'
            with self.assertRaises((ValueError,FileNotFoundError)):
                verify_bundle(root,manifest,fixture=True)


    def test_forged_receipt_and_unused_history_attribute_are_not_observations(self):
        import json
        from azure.fabric.content import verify_export_artifacts,verify_observed_export,content_digest
        from azure.fabric.archive import sha_file
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            root=Path(directory);manifest=synthetic_bundle(root)
            receipt=verify_export_artifacts(manifest,root/'receipt.json',root/'employer_history.jsonl')
            native_observation=copy.deepcopy(receipt)
            row=json.loads((root/'employer_history.jsonl').read_text());row['postcode']='Changed unused history attribute'
            (root/'employer_history.jsonl').write_text(json.dumps(row)+'\n')
            manifest['history_sha256']=sha_file(root/'employer_history.jsonl')
            next(r for r in manifest['references'] if r['table']=='employer_history')['sha256']=manifest['history_sha256']
            with self.assertRaisesRegex(ValueError,'artifact/content'):verify_export_artifacts(manifest,root/'receipt.json',root/'employer_history.jsonl')
            receipt['history']=dict(content_digest('history',[row]),bytes_sha256=manifest['history_sha256'])
            (root/'receipt.json').write_text(json.dumps(receipt)+'\n');manifest['history_export_receipt_sha256']=sha_file(root/'receipt.json')
            forged=verify_export_artifacts(manifest,root/'receipt.json',root/'employer_history.jsonl')
            with self.assertRaisesRegex(ValueError,'history'):verify_observed_export(native_observation,forged)
            for key in ('source','snapshot','lineage','queries'):
                forged=copy.deepcopy(native_observation);forged[key]='changed'
                with self.assertRaises(ValueError):verify_observed_export(native_observation,forged)

    def test_complete_fact_and_dimension_content_rejection(self):
        from unittest.mock import patch
        from azure.fabric.content import SCHEMA,SUBJECT,content_digest
        empty={name:content_digest(name,[]) for name in SUBJECT}
        fact={field.split(':')[0]:None for field in SCHEMA['fact_lca_case']};fact.update(case_number='A',employer_id='E1',employer_history_status='PRE_SNAPSHOT_HISTORY_UNKNOWN')
        dim={field.split(':')[0]:None for field in SCHEMA['dim_employer']};dim.update(employer_id='E1',postcode='Old')
        reference=dict(content=dict(empty,fact_lca_case=content_digest('fact_lca_case',[fact]),dim_employer=content_digest('dim_employer',[dim])),metrics=dict(case_count=1,certified_rate=0,wage_p50=None,wage_p90=None))
        for table,row,field,value in [('fact_lca_case',fact,'employer_id','E2'),('dim_employer',dim,'postcode','New')]:
            actual=copy.deepcopy(reference);actual['content'][table]=content_digest(table,[dict(row,**{field:value})])
            self.assertEqual(reference['metrics'],actual['metrics'])
            with patch.object(native,'fabric_receipt',return_value=actual):
                with self.assertRaisesRegex(ValueError,'full subject'):native.accept_target(None,{},reference)

    def test_python_javascript_exact_content_and_native_observe(self):
        import json
        import subprocess
        from azure.fabric.content import SCHEMA,content_digest,history_bytes
        from azure.tests.test_pipeline import history
        rows={table:[{field.split(':')[0]:None for field in fields}] for table,fields in SCHEMA.items()}
        rows['history']=[history()];rows['history'][0].update(source_fiscal_year='2025',is_source_active='1',employer_name='雇用😀')
        rows['fact_lca_case'][0].update(case_number='A',worker_positions='99999999999999999999999999999999999999',annual_wage='1.2300',employer_id='E1')
        script="""const {observe}=require('./azure/fabric/snowflake.cjs');const {digest,historyBytes}=require('./azure/fabric/content.cjs');const rows=JSON.parse(require('fs').readFileSync(0,'utf8'));const lineage={registry_sha256:'a'.repeat(64),model_sha256:'b'.repeat(64),xref_sha256:'c'.repeat(64),input_records_sha256:'d'.repeat(64),input_occurrences_sha256:'e'.repeat(64),namespace:'fixture',input_kind:'fixture',acceptance_status:'AGENT_WEAK_REFERENCE'};const reader={preflight:async()=>({kind:'SYNTHETIC'}),read:async name=>name==='identity'?[{account:'fixture',region:'fixture',user:'fixture',role:'fixture'}]:name==='lineage'?[{manifest_json:JSON.stringify(lineage)}]:name==='metrics'?[{case_count:'1',certified_rate:'0',wage_p50:null,wage_p90:null}]:rows[name]};observe(reader,'2025-01-01T00:00:00Z').then(r=>process.stdout.write(JSON.stringify({receipt:r.receipt,bytes:r.bytes.toString(),digests:Object.fromEntries(Object.entries(rows).map(([n,r])=>[n,digest(n,r)]))})));"""
        result=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(rows),text=True,capture_output=True,check=True)
        actual=json.loads(result.stdout)
        self.assertEqual(actual['digests'],{name:content_digest(name,r) for name,r in rows.items()})
        self.assertEqual(actual['bytes'].encode(),history_bytes(rows['history']))
        self.assertNotIn('identity',actual['receipt'])
        self.assertEqual(actual['receipt']['content']['fact_lca_case'],actual['digests']['fact_lca_case'])
