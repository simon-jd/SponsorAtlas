"""Explicit opt-in native Fabric SQL adapter; missing configuration fails closed.

No dependency installation or authentication acquisition is performed here.
Tokens are externally provisioned owner-only files. Entra/Fabric verify signatures;
local claim checks bind intended tenant/audience/principal and expiration first.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import urllib.request

from azure.fabric.contract import compare_metrics
from azure.fabric.content import SUBJECT,columns,content_digest,verify_export_artifacts,verify_observed_export
from azure.fabric.archive import sha_file,validate_manifest,OFFICIAL,contained
from azure.fabric.warehouse import HERE, assert_checks, identifier, rebuild,sql_digest

GUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
WITNESS=columns('fact_lca_case')


def validate_config(config,env):
    required={'tenant_id','workspace_id','warehouse_id','lakehouse_id','principal_id','warehouse','lakehouse','server','fabric_trial_expires_at','snapshot','identity','sql_token_file','fabric_token_file','manifest_file','history_file'}
    if not required<=config.keys(): raise ValueError('native configuration incomplete')
    if env.get('AZURE_PORT_NATIVE_OPT_IN')!='1': raise ValueError('explicit native opt-in required')
    for field in ('tenant_id','workspace_id','warehouse_id','lakehouse_id','principal_id'):
        if not re.fullmatch(GUID,config[field]): raise ValueError('invalid native identity')
    if config['warehouse_id']==config['lakehouse_id']: raise ValueError('Warehouse must differ from Lakehouse')
    for field in ('warehouse','lakehouse'): identifier(config[field])
    if config['warehouse']==config['lakehouse']: raise ValueError('writable Warehouse required')
    if not re.fullmatch(r'[a-z0-9-]+\.datawarehouse\.fabric\.microsoft\.com',config['server']): raise ValueError('unapproved SQL endpoint')
    for field in ('snapshot','fabric_trial_expires_at'):
        if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',config[field]): raise ValueError('explicit UTC timestamps required')
    now=datetime.now(timezone.utc)
    expiry=datetime.fromisoformat(config['fabric_trial_expires_at'].replace('Z','+00:00'))
    snapshot=datetime.fromisoformat(config['snapshot'].replace('Z','+00:00'))
    if expiry.timestamp()-now.timestamp()<600 or snapshot>now: raise ValueError('trial inactive or snapshot in future')
    identity=config['identity']
    if set(identity)!= {'scope','manifest_sha256','model_version','registry_sha256','history_sha256'} or identity['scope']!='LCA_FY2024_Q4+FY2025_Q4': raise ValueError('source identity incomplete')
    for field in ('manifest_sha256','registry_sha256','history_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',identity[field]): raise ValueError('source identity digest missing')
    if identity['model_version']!='sponsoratlas-lca-port-v1': raise ValueError('unknown model version')
    for field in ('SNOWFLAKE_ACCOUNT','SNOWFLAKE_PRIVATE_KEY_FILE','SNOWFLAKE_TRIAL_STARTED_AT','SNOWFLAKE_TRIAL_EXPIRES_AT'):
        if not env.get(field): raise ValueError('Snowflake key-pair configuration incomplete')
    if any(env.get(k) for k in ('SNOWFLAKE_PASSWORD','SNOWFLAKE_PWD','SNOWFLAKE_PASS')): raise ValueError('password forbidden')
    return config


def token(path,config,audience):
    path=Path(path)
    if path.stat().st_mode & 0o077: raise ValueError('token file must be owner-only')
    raw=path.read_text().strip()
    try:
        parts=raw.split('.')
        claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    except Exception as exc: raise ValueError('invalid Entra token') from exc
    if claims.get('tid')!=config['tenant_id'] or claims.get('oid')!=config['principal_id'] or claims.get('aud') not in audience or claims.get('exp',0)<time.time()+600:
        raise ValueError('token tenant/audience/principal/lifetime mismatch')
    return raw


def warehouse_metadata(config,bearer):
    url=f"https://api.fabric.microsoft.com/v1/workspaces/{config['workspace_id']}/warehouses/{config['warehouse_id']}"
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+bearer})
    with urllib.request.urlopen(request,timeout=30) as response: meta=json.load(response)
    if meta.get('id')!=config['warehouse_id'] or meta.get('type')!='Warehouse' or meta.get('displayName')!=config['warehouse']:
        raise ValueError('control-plane item is not intended writable Warehouse')
    if meta.get('properties',{}).get('connectionString')!=config['server']:
        raise ValueError('control-plane SQL endpoint mismatch')
    return {'id':meta['id'],'type':meta['type'],'displayName':meta['displayName']}


def witness_digest(rows):
    # Sorted in Python by UTF-8 strings, independent of database collation order.
    rows=sorted([[None if x is None else str(x) for x in row] for row in rows],key=lambda r:r[0])
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def fabric_receipt(cursor,config):
    """Measure using the caller's transaction; never commit an unaccepted target."""
    assert_checks(cursor,'checks.sql',config['lakehouse'])
    rows=cursor.execute('SELECT scope,manifest_sha256,model_version,registry_sha256,history_sha256 FROM MARTS.port_identity').fetchall()
    if len(rows)!=1: raise ValueError('expected one committed source identity')
    identity=dict(zip(('scope','manifest_sha256','model_version','registry_sha256','history_sha256'),rows[0]))
    if identity!=config['identity']: raise ValueError('committed Fabric identity mismatch')
    values=cursor.execute((HERE/'verify-fabric.sql').read_text()).fetchone()
    result=dict(zip(('case_count','certified_rate','wage_p50','wage_p90'),values))
    if result['case_count']<=0: raise ValueError('empty native source cannot establish official scope')
    occurrences=cursor.execute('SELECT SUM(source_occurrence_count) FROM MARTS.fact_lca_case').fetchone()[0]
    if occurrences!=sum(x[0] for x in OFFICIAL.values()): raise ValueError('full archive occurrence accounting mismatch')
    releases=cursor.execute('SELECT DISTINCT source_release,source_sha256,fiscal_year FROM MARTS.fact_lca_case').fetchall()
    if {tuple(r) for r in releases}!={(release,values[1],int(release[2:6])) for release,values in OFFICIAL.items()}: raise ValueError('actual native source releases differ')
    content={name:content_digest(name,cursor.execute('SELECT '+','.join(columns(name))+' FROM MARTS.'+name).fetchall()) for name in SUBJECT}
    return {'kind':'ACTUAL_FABRIC_WAREHOUSE_SQL','declared_bundle_identity':identity,'metrics':result,'content':content,'snapshot_mode':'Warehouse transaction snapshot'}


def accept_target(cursor,config,reference):
    actual=fabric_receipt(cursor,config)
    if reference['content']!=actual['content']:raise ValueError('actual full subject content differs')
    comparison=compare_metrics(reference['metrics'],actual['metrics'])
    return actual,comparison


def verify_or_load(connection,config,reference,manifest,*,load=False,bootstrap=False):
    accept=lambda cursor:accept_target(cursor,config,reference)
    if load:
        return rebuild(connection,config['lakehouse'],config['identity'],bootstrap=bootstrap,manifest=manifest,accept=accept)
    cursor=connection.cursor();cursor.execute('BEGIN TRANSACTION')
    try:
        accepted=accept(cursor);cursor.execute('COMMIT');return accepted
    except BaseException:
        cursor.execute('ROLLBACK');raise
    finally:cursor.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--load',action='store_true')
    parser.add_argument('--bootstrap',action='store_true')
    args=parser.parse_args()
    # Fail before optional imports, process launch, token reads or network.
    config=validate_config(json.loads(args.config.read_text()),os.environ)
    if args.bootstrap and not args.load: raise ValueError('bootstrap requires load')
    if args.output.exists(): raise ValueError('refuse overwriting native receipt')
    if sha_file(config['manifest_file'])!=config['identity']['manifest_sha256']:raise ValueError('native manifest digest mismatch')
    manifest=validate_manifest(json.loads(Path(config['manifest_file']).read_text()))
    if manifest['warehouse_sql_sha256']!=sql_digest():raise ValueError('Warehouse model SQL version differs from source bundle')
    if manifest['history_snapshot_utc']!=config['snapshot'] or any(manifest[k]!=config['identity'][k] for k in ('scope','model_version','registry_sha256','history_sha256')):raise ValueError('native snapshot/manifest identity differs')
    export_receipt=verify_export_artifacts(manifest,contained(Path(config['manifest_file']).parent,manifest['history_export_receipt_path']),config['history_file'])
    sql_token=token(config['sql_token_file'],config,{'https://database.windows.net/','https://database.windows.net'})
    api_token=token(config['fabric_token_file'],config,{'https://api.fabric.microsoft.com','https://analysis.windows.net/powerbi/api'})
    import pyodbc  # intentionally optional; no fallback driver/install
    if 'ODBC Driver 18 for SQL Server' not in pyodbc.drivers(): raise ValueError('ODBC18 required')
    metadata=warehouse_metadata(config,api_token)
    # Validate the pinned native source before any target mutation.
    env=dict(os.environ,SEMANTIC_SOURCE='snowflake',SEMANTIC_SNOWFLAKE_OPT_IN='1')
    reference=json.loads(subprocess.check_output(['node',str(HERE/'snowflake.cjs'),config['snapshot']],env=env,timeout=150,text=True))
    verify_observed_export(reference,export_receipt)
    encoded=sql_token.encode('utf-16-le')
    connection=pyodbc.connect(f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={config['server']};DATABASE={config['warehouse']};Encrypt=yes;TrustServerCertificate=no;",attrs_before={1256:struct.pack('<I',len(encoded))+encoded},autocommit=True,timeout=30)
    connection.timeout=120
    started=time.monotonic()
    try:
        cursor=connection.cursor()
        try:
            name,collation=cursor.execute("SELECT DB_NAME(),CAST(DATABASEPROPERTYEX(DB_NAME(),'Collation') AS varchar(128))").fetchone()
            if name!=config['warehouse'] or not collation.endswith('_UTF8'):raise ValueError('wrong database or non-UTF8 persistence')
        finally:cursor.close()
        actual,comparison=verify_or_load(connection,config,reference,manifest,load=args.load,bootstrap=args.bootstrap)
    finally: connection.close()
    receipt={'kind':'ACTUAL_SNOWFLAKE_VS_FABRIC_SQL','comparison':comparison,'reference':reference,'actual':actual,'warehouse':metadata,'elapsed_seconds':time.monotonic()-started}
    args.output.write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    print(json.dumps({'kind':receipt['kind'],'status':'PASS','receipt':str(args.output)}))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        # Do not serialize driver/auth exception text that can contain credentials.
        print('Native acceptance OPEN: '+type(exc).__name__+'; configuration/permission/driver/query gate failed',file=sys.stderr)
        raise SystemExit(1)
