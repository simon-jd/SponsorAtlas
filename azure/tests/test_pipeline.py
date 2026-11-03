import copy
import base64
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from azure.fabric.archive import validate_manifest,verify_bundle,schema_digest,sha_file,contained,REFERENCE_TABLES,history_row
from azure.fabric.native import validate_config,token,witness_digest
from azure.fabric.warehouse import statements,rebuild
from azure.fabric.plan_bundle import code_zip
from azure.fabric.content import content_digest
from azure.tests.test_port import ROOT,envelope,SHA


def synthetic_bundle(root):
    manifest=dict(kind='SYNTHETIC_FIXTURE',scope='LCA_FY2024_Q4+FY2025_Q4',model_version='sponsoratlas-lca-port-v1',archives=[],references=[],registry_sha256=SHA,model_sha256=SHA,history_snapshot_utc='2025-01-01T00:00:00Z',history_export_receipt_sha256=SHA,history_semantics='CAPTURE_TIME_UTC',er_acceptance_status='AGENT_WEAK_REFERENCE',er_quality_disclosure='Human adjudication OPEN',mapping_path='mapping.csv',code_sha256=SHA,warehouse_sql_sha256=SHA)
    for year in (2024,2025):
        row=envelope();row['source_release']=f'FY{year}_Q4';name=f'FY{year}_Q4_{SHA}.jsonl'
        path=root/name;path.write_text(json.dumps(row)+'\n')
        manifest['archives'].append(dict(release=row['source_release'],path=name,sha256=sha_file(path),original_sha256=SHA,schema_sha256=schema_digest(json.loads(row['payload_json'])),rows=1,url=row['source_url']))
    for table in sorted(REFERENCE_TABLES):
        row=history() if table=='employer_history' else {'key':'synthetic'}
        path=root/(table+'.jsonl');path.write_text(json.dumps(row)+'\n')
        manifest['references'].append(dict(table=table,path=path.name,sha256=sha_file(path),schema_sha256=schema_digest(row),rows=1,format='jsonl'))
    manifest['xref_sha256']=next(x['sha256'] for x in manifest['references'] if x['table']=='employer_xref')
    manifest['history_sha256']=next(x['sha256'] for x in manifest['references'] if x['table']=='employer_history')
    receipt=dict(kind='ACTUAL_SNOWFLAKE_HISTORY_EXPORT',source=dict(account='synthetic',region='synthetic',user='synthetic',role='synthetic'),snapshot=manifest['history_snapshot_utc'],relation='ANALYTICS.SNAPSHOTS.snap_employer',lineage=dict(registry_sha256=SHA,model_sha256=SHA,acceptance_status=manifest['er_acceptance_status']),history=dict(content_digest('history',[history()]),bytes_sha256=manifest['history_sha256']),queries=dict(history=SHA,lineage=SHA))
    (root/'receipt.json').write_text(json.dumps(receipt)+'\n');manifest['history_export_receipt_path']='receipt.json';manifest['history_export_receipt_sha256']=sha_file(root/'receipt.json')
    (root/'mapping.csv').write_text('synthetic\n');manifest['mapping_sha256']=sha_file(root/'mapping.csv')
    return manifest


def history():
    row={k:None for k in ('employer_name','state','country','city','postcode','attribute_source_system','attribute_source_key','source_fiscal_year','source_observation_period','resolution_status','match_method','identity_quality_status','is_source_active')}
    row.update(dbt_scd_id='a'*32,employer_id='E',dbt_valid_from='2025-01-01T00:00:00.123456',dbt_valid_to=None)
    return row


def native_config():
    guid='12345678-1234-1234-1234-123456789012'
    config={k:guid for k in ('tenant_id','workspace_id','warehouse_id','principal_id')}
    config.update(lakehouse_id='12345678-1234-1234-1234-123456789013',warehouse='LcaWarehouse',lakehouse='LcaLakehouse',server='abc.datawarehouse.fabric.microsoft.com',fabric_trial_expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),snapshot='2025-01-01T00:00:00Z',sql_token_file='not-opened',fabric_token_file='not-opened',manifest_file='not-opened',history_file='not-opened')
    config['identity']=dict(scope='LCA_FY2024_Q4+FY2025_Q4',manifest_sha256=SHA,registry_sha256=SHA,history_sha256=SHA,model_version='sponsoratlas-lca-port-v1')
    env={k:'configured' for k in ('SNOWFLAKE_ACCOUNT','SNOWFLAKE_PRIVATE_KEY_FILE','SNOWFLAKE_TRIAL_STARTED_AT','SNOWFLAKE_TRIAL_EXPIRES_AT')};env['AZURE_PORT_NATIVE_OPT_IN']='1'
    return config,env


class Pipeline(unittest.TestCase):
    def test_native_source_failure_never_connects_or_mutates_target(self):
        import sys
        from azure.fabric import native
        from azure.fabric.warehouse import sql_digest
        config,env=native_config()
        manifest=dict(history_export_receipt_path='receipt.json',history_snapshot_utc=config['snapshot'],warehouse_sql_sha256=sql_digest(),**{k:config['identity'][k] for k in ('scope','model_version','registry_sha256','history_sha256')})
        driver=Mock();driver.drivers.return_value=['ODBC Driver 18 for SQL Server']
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            cfg=Path(directory)/'config.json';cfg.write_text(json.dumps(config));source=Path(directory)/'manifest.json';source.write_text('{}')
            config['manifest_file']=str(source);cfg.write_text(json.dumps(config))
            with patch.dict(sys.modules,{'pyodbc':driver}),patch.dict('os.environ',env,clear=True),patch.object(sys,'argv',['native','--config',str(cfg),'--output',str(Path(directory)/'receipt.json'),'--load']),patch.object(native,'sha_file',return_value=SHA),patch.object(native,'validate_manifest',return_value=manifest),patch.object(native,'verify_export_artifacts',return_value={}),patch.object(native,'token',return_value='synthetic'),patch.object(native,'warehouse_metadata',return_value={}),patch.object(native.subprocess,'check_output',side_effect=RuntimeError('synthetic native source failure')):
                with self.assertRaisesRegex(RuntimeError,'source failure'):native.main()
            driver.connect.assert_not_called()
    def test_python_notebook_and_native_js_syntax(self):
        import ast
        import subprocess
        for path in (ROOT/'azure/fabric').glob('*.py'):ast.parse(path.read_text(),filename=str(path))
        checked=subprocess.run(['node','--check',str(ROOT/'azure/fabric/snowflake.cjs')],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stderr)
    def test_notebook_code_zip_is_deterministic_and_self_contained(self):
        import io
        import zipfile
        a=code_zip();self.assertEqual(a,code_zip())
        with zipfile.ZipFile(io.BytesIO(a)) as archive:
            self.assertEqual(set(archive.namelist()),{'sponsoratlas_port/__init__.py','sponsoratlas_port/archive.py','sponsoratlas_port/contract.py','sponsoratlas_port/content.py','sponsoratlas_port/content-schema.json'})
            self.assertEqual(archive.read('sponsoratlas_port/contract.py'),(ROOT/'azure/fabric/contract.py').read_bytes())
    def test_notebook_zip_import_in_isolated_python(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            zipped=Path(directory)/'port.zip';zipped.write_bytes(code_zip())
            script="import sys;sys.path.insert(0,sys.argv[1]);from sponsoratlas_port.archive import verify_bundle;from sponsoratlas_port.contract import normalize;from sponsoratlas_port.content import columns;assert len(columns('fact_lca_case'))==35"
            result=subprocess.run([sys.executable,'-I','-c',script,str(zipped)],cwd=directory,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
    def test_tokens_bound_to_tenant_audience_principal_and_lifetime(self):
        config,_=native_config()
        claims=dict(tid=config['tenant_id'],oid=config['principal_id'],aud='https://database.windows.net/',exp=datetime.now(timezone.utc).timestamp()+1200)
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as directory:
            path=Path(directory)/'token'
            def write(payload):
                encoded=base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
                path.write_text('synthetic.'+encoded+'.signature');path.chmod(0o600)
            write(claims)
            self.assertTrue(token(path,config,{'https://database.windows.net/'}).startswith('synthetic.'))
            for field,value in [('tid','wrong'),('oid','wrong'),('aud','https://wrong'),('exp',0)]:
                write(dict(claims,**{field:value}))
                with self.assertRaises(ValueError):token(path,config,{'https://database.windows.net/'})
            write(claims);path.chmod(0o644)
            with self.assertRaises(ValueError):token(path,config,{'https://database.windows.net/'})
    def test_capture_history_precision_and_required_full_payload(self):
        row=history();history_row(row)
        for value in ('2025-01-01T00:00:00.1234567','2025-01-01T00:00:00+05:00','invalid'):
            with self.assertRaises(ValueError):history_row(dict(row,dbt_valid_from=value))
        with self.assertRaises(ValueError):history_row(dict(row,employer_name='雇'*3000))
        row.pop('identity_quality_status')
        with self.assertRaises(ValueError):history_row(row)
    def test_binary_adf_dependencies_auth_and_no_delete(self):
        template=json.loads((ROOT/'azure/adf-pipeline.json').read_text());resources=template['resources']
        self.assertEqual(len(resources),6)
        lake=next(r for r in resources if r['properties'].get('type')=='Lakehouse')
        self.assertEqual(lake['properties']['typeProperties']['servicePrincipalCredentialType'],'ServicePrincipalCert')
        pipe=next(r for r in resources if r['type'].endswith('/pipelines'))
        copy=pipe['properties']['activities'][0]['typeProperties']['activities'][0]
        self.assertEqual(copy['typeProperties']['source']['type'],'BinarySource')
        self.assertEqual(copy['typeProperties']['sink']['storeSettings']['type'],'LakehouseWriteSettings')
        self.assertFalse(copy['typeProperties']['source']['storeSettings']['deleteFilesAfterCompletion'])
        self.assertEqual(copy['typeProperties']['parallelCopies'],1)
        self.assertEqual(pipe['properties']['concurrency'],1)
        self.assertNotIn('connectionString',json.dumps(template))

    def test_bundle_digests_schema_rows_source_kind_and_traversal(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'azure/fabric/evidence') as name:
            root=Path(name);manifest=synthetic_bundle(root)
            self.assertTrue(verify_bundle(root,manifest,fixture=True))
            with self.assertRaisesRegex(ValueError,'kind'):validate_manifest(manifest)
            wrong=copy.deepcopy(manifest);wrong['archives'][0]['rows']=2
            with self.assertRaisesRegex(ValueError,'row count'):verify_bundle(root,wrong,fixture=True)
            wrong=copy.deepcopy(manifest);wrong['archives'][0]['schema_sha256']='b'*64
            with self.assertRaisesRegex(ValueError,'schema'):verify_bundle(root,wrong,fixture=True)
            wrong=copy.deepcopy(manifest);wrong['registry_sha256']=''
            with self.assertRaisesRegex(ValueError,'lineage'):validate_manifest(wrong,fixture=True)
            path=root/manifest['archives'][0]['path'];path.write_text(path.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'digest'):verify_bundle(root,manifest,fixture=True)
        for path in ('../outside','/tmp/out','a/../../out'):
            with self.assertRaises(ValueError):contained('/bundle',path)

    def test_sql_parser_preserves_literals_and_rejects_incomplete(self):
        self.assertEqual(statements("-- comment;\nSELECT ';--', 'it''s'; /* ; */ SELECT [a;b];"),["SELECT ';--', 'it''s'",'SELECT [a;b]'])
        for sql in ("SELECT 'oops",'/* oops'):
            with self.assertRaises(ValueError):statements(sql)

    def test_missing_identity_permission_and_ddl_roll_back(self):
        config,_=native_config()
        config['identity']['scope']='SYNTHETIC'
        for cause in ('identity','permission','ddl'):
            connection=Mock();cursor=connection.cursor.return_value
            identity=[tuple(config['identity'][k] for k in ('scope','manifest_sha256','model_version','registry_sha256','history_sha256'))]
            cursor.execute.return_value=cursor;cursor.fetchall.return_value=identity if cause!='identity' else []
            cursor.fetchone.return_value=(0,)
            if cause!='identity':
                def execute(sql):
                    if (cause=='permission' and 'FROM LcaLakehouse.dbo.port_lca' in sql) or (cause=='ddl' and sql.startswith('CREATE SCHEMA')):raise PermissionError('synthetic denied')
                    return cursor
                cursor.execute.side_effect=execute
            with self.assertRaises((ValueError,PermissionError)):rebuild(connection,'LcaLakehouse',config['identity'],bootstrap=True)
            self.assertIn(unittest.mock.call('ROLLBACK'),cursor.execute.call_args_list)
            self.assertNotIn(unittest.mock.call('COMMIT'),cursor.execute.call_args_list)
            cursor.close.assert_called_once()

    def test_config_wrong_tenant_endpoint_snapshot_and_password(self):
        good,env=native_config();validate_config(good,env)
        for field,value in [('server','evil.example'),('lakehouse_id',good['warehouse_id']),('warehouse','x];DROP TABLE t'),('fabric_trial_expires_at','2000-01-01T00:00:00Z'),('tenant_id','not-a-guid')]:
            bad=copy.deepcopy(good);bad[field]=value
            with self.assertRaises(ValueError):validate_config(bad,env)
        with self.assertRaises(ValueError):validate_config(good,dict(env,SNOWFLAKE_PASSWORD='forbidden'))
        with self.assertRaises(ValueError):validate_config(good,{})

    def test_native_load_rejects_unsynchronized_inputs_before_deleting_marts(self):
        config,_=native_config();connection=Mock();cursor=connection.cursor.return_value
        cursor.execute.return_value=cursor;cursor.fetchone.return_value=(0,)
        identity=tuple(config['identity'][k] for k in ('scope','manifest_sha256','model_version','registry_sha256','history_sha256'))
        cursor.fetchall.side_effect=[[identity],[('FY2025_Q4',SHA,1)]]
        manifest={'archives':[dict(release='FY2025_Q4',original_sha256=SHA,rows=2)],'references':[]}
        with self.assertRaisesRegex(ValueError,'synchronization incomplete'):rebuild(connection,'LcaLakehouse',config['identity'],manifest=manifest)
        self.assertFalse(any(call.args[0].startswith('DELETE') for call in cursor.execute.call_args_list))
        self.assertIn(unittest.mock.call('ROLLBACK'),cursor.execute.call_args_list)

    def test_witness_changes_with_source_row_history_and_snapshot(self):
        rows=[['A','FY2025_Q4',SHA,2,2025,None,'PRE_SNAPSHOT_HISTORY_UNKNOWN',2]]
        expected=witness_digest(rows)
        for index in range(len(rows[0])):
            mutated=copy.deepcopy(rows);mutated[0][index]='different'
            self.assertNotEqual(expected,witness_digest(mutated))
