"""Immutable source bundle validation shared by local tests and Fabric notebook.

Only an explicit notebook/native call reads archives. Local tests use tiny files.
"""
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from .content import verify_export_artifacts

SCOPE='LCA_FY2024_Q4+FY2025_Q4'
REFERENCE_TABLES={'county_fips','ref_soc_2018','ref_county_2023','ref_county_cbsa_2023','employer_xref','employer_history'}
OFFICIAL={
 'FY2024_Q4':(120897,'23bc116e679e7ff10b9ac947dc4faf7942ae59ef1c3ffb5a10be61c4ae31ba30','89818451477136344251a27c9e8dc8b168e7570af9e204de03f2d7b7c582dbab'),
 'FY2025_Q4':(118580,'81ebf97d471014b91ae685cb7eb505369d7c80af2de67944ddd7531814976370','89b84a07a177997a8e41be2db067e788438707a3d01cd95a7d046bd021e120ae')}


def sha_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def contained(root,relative):
    if not isinstance(relative,str) or not relative or relative.startswith('/') or '..' in Path(relative).parts or '\\' in relative:
        raise ValueError('unsafe bundle path')
    path=(Path(root)/relative).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise ValueError('bundle path escapes root')
    return path


def validate_manifest(manifest,*,fixture=False):
    if manifest.get('kind')!=('SYNTHETIC_FIXTURE' if fixture else 'OFFICIAL_ARCHIVE_SUBJECT_PORT'):
        raise ValueError('source kind mismatch')
    if manifest.get('scope')!=SCOPE or manifest.get('model_version')!='sponsoratlas-lca-port-v1': raise ValueError('scope/version mismatch')
    archives=manifest.get('archives',[]);refs=manifest.get('references',[])
    if len(archives)!=2 or {a['release'] for a in archives}!=set(OFFICIAL): raise ValueError('exact release set required')
    if len(refs)!=len(REFERENCE_TABLES) or {r['table'] for r in refs}!=REFERENCE_TABLES: raise ValueError('complete dependent inputs required')
    for a in archives:
        for field in ('original_sha256','sha256','schema_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',a[field]): raise ValueError('archive digest missing')
        if type(a['rows']) is not int or a['rows']<=0: raise ValueError('archive row count missing')
        if not fixture and (a['rows'],a['original_sha256'],a['sha256'])!=OFFICIAL[a['release']]: raise ValueError('official release/archive identity mismatch')
        if not a['url'].startswith('https://www.dol.gov/'): raise ValueError('official source URL required')
        if a['release'] not in a['path'] or a['original_sha256'] not in a['path']: raise ValueError('archive name must contain release/original SHA')
    for r in refs:
        for field in ('sha256','schema_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',r[field]): raise ValueError('reference digest missing')
        if r['format'] not in ('csv','jsonl'): raise ValueError('reference format unsupported')
        if type(r['rows']) is not int or r['rows']<1: raise ValueError('reference count missing')
    for field in ('registry_sha256','model_sha256','xref_sha256','history_sha256','mapping_sha256','code_sha256','warehouse_sql_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',manifest.get(field,'')): raise ValueError('registry/history/mapping lineage incomplete')
    by_table={r['table']:r for r in refs}
    if by_table['employer_history']['sha256']!=manifest['history_sha256'] or by_table['employer_xref']['sha256']!=manifest['xref_sha256']: raise ValueError('ER/history lineage differs')
    if manifest.get('history_semantics')!='CAPTURE_TIME_UTC' or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',manifest.get('history_snapshot_utc','')) or not re.fullmatch('[0-9a-f]{64}',manifest.get('history_export_receipt_sha256','')):
        raise ValueError('immutable captured history export required')
    if not manifest.get('er_acceptance_status') or 'human' not in manifest.get('er_quality_disclosure','').lower():
        raise ValueError('ER weak-reference/human gate disclosure required')
    paths=[a['path'] for a in archives]+[r['path'] for r in refs]+[manifest['mapping_path'],manifest['history_export_receipt_path']]
    if len(set(paths))!=len(paths): raise ValueError('duplicate bundle paths')
    for path in paths: contained('/bundle',path)
    return manifest


def schema_digest(columns):
    return hashlib.sha256(json.dumps(sorted(columns),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def history_row(row):
    required={'dbt_scd_id','employer_id','employer_name','state','country','city','postcode','attribute_source_system','attribute_source_key','source_fiscal_year','source_observation_period','resolution_status','match_method','identity_quality_status','is_source_active','dbt_valid_from','dbt_valid_to'}
    if set(row)!=required:raise ValueError('capture history schema differs')
    if not isinstance(row['dbt_scd_id'],str) or not re.fullmatch('[0-9a-f]{32}',row['dbt_scd_id']) or not row['employer_id']:raise ValueError('capture history key missing')
    for key in ('dbt_valid_from','dbt_valid_to'):
        value=row[key]
        if value is None and key=='dbt_valid_to':continue
        # Export UTC timestamp_ntz as ISO text: no timezone conversion or >6-digit rounding.
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d{1,6})?',value):raise ValueError('history UTC microsecond precision required')
        datetime.fromisoformat(value)
    if row['dbt_valid_to'] is not None and datetime.fromisoformat(row['dbt_valid_to'])<=datetime.fromisoformat(row['dbt_valid_from']):raise ValueError('capture history interval invalid')
    if any(isinstance(v,str) and len(v.encode('utf-8'))>8000 for v in row.values()):raise ValueError('history UTF-8 width exceeded')


def verify_bundle(root,manifest,*,fixture=False):
    validate_manifest(manifest,fixture=fixture)
    for entry in manifest['archives']+manifest['references']:
        path=contained(root,entry['path'])
        if sha_file(path)!=entry['sha256']: raise ValueError('bundle digest mismatch: '+entry['path'])
        count=0;columns=None
        with path.open() as stream:
            iterator=csv.DictReader(stream) if entry.get('format')=='csv' else (json.loads(line) for line in stream)
            for row in iterator:
                count+=1
                observed=set(json.loads(row['payload_json'])) if 'release' in entry else set(row)
                if columns is None: columns=observed
                if observed!=columns: raise ValueError('schema drift in bundle')
                if entry.get('table')=='employer_history':history_row(row)
                if 'release' in entry and (row['source_release']!=entry['release'] or row['source_sha256']!=entry['original_sha256'] or row['source_url']!=entry['url'] or type(row['source_row_number']) is not int or row['source_row_number']<1): raise ValueError('original release lineage differs')
        if count!=entry['rows'] or schema_digest(columns or [])!=entry['schema_sha256']: raise ValueError('bundle row count/schema differs')
    if sha_file(contained(root,manifest['mapping_path']))!=manifest['mapping_sha256']: raise ValueError('annual mapping digest mismatch')
    history=next(r for r in manifest['references'] if r['table']=='employer_history')
    verify_export_artifacts(manifest,contained(root,manifest['history_export_receipt_path']),contained(root,history['path']))
    return True
