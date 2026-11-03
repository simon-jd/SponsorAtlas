"""Lossless, typed content witnesses shared by native acceptance and export checks."""
from datetime import date,datetime
from decimal import Decimal
from importlib.resources import files
import hashlib
import json
from pathlib import Path
import re

SCHEMA=json.loads(files(__package__).joinpath('content-schema.json').read_text())
SUBJECT=tuple(k for k in SCHEMA if k!='history')


def columns(table):return [field.split(':')[0] for field in SCHEMA[table]]


def scalar(value,kind):
    if value is None:return None
    if kind=='bool':
        if value is True or str(value).lower() in ('1','true'):return '1'
        if value is False or str(value).lower() in ('0','false'):return '0'
        raise ValueError('nonboolean content')
    if kind=='number':
        if isinstance(value,bool) or isinstance(value,float):raise ValueError('lossy numeric content')
        value=format(value,'f') if isinstance(value,Decimal) else str(value)
        if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value):raise ValueError('invalid exact numeric content')
        number=Decimal(value)
        result=format(number,'f').lstrip('+')
        if '.' in result:result=result.rstrip('0').rstrip('.')
        return '0' if number==0 else result
    if kind=='timestamp':
        if isinstance(value,datetime):
            if value.tzinfo is not None:raise ValueError('UTC naive capture timestamp required')
            return value.isoformat(timespec='microseconds')
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d{1,6})?',value):raise ValueError('invalid timestamp content')
        return datetime.fromisoformat(value).isoformat(timespec='microseconds')
    if kind=='date':
        value=value.isoformat() if type(value) is date else value
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\d',value):raise ValueError('invalid date content')
        return date.fromisoformat(value).isoformat()
    if not isinstance(value,str):raise ValueError('nontext content')
    return value


def canonical_rows(table,rows):
    fields=SCHEMA[table];names=columns(table);result=[]
    for row in rows:
        if isinstance(row,dict):
            if set(row)!=set(names):raise ValueError('content schema mismatch')
            row=[row[k] for k in names]
        if len(row)!=len(fields):raise ValueError('content width mismatch')
        result.append([scalar(value,field.split(':')[1]) for value,field in zip(row,fields)])
    return sorted(result,key=lambda row:json.dumps(row,ensure_ascii=False,separators=(',',':')).encode())


def content_digest(table,rows):
    rows=canonical_rows(table,rows)
    data=json.dumps(dict(columns=SCHEMA[table],rows=rows),ensure_ascii=False,separators=(',',':')).encode()
    return {'rows':len(rows),'sha256':hashlib.sha256(data).hexdigest()}


def history_bytes(rows):
    names=columns('history')
    return ''.join(json.dumps(dict(zip(names,row)),ensure_ascii=False,separators=(',',':'))+'\n' for row in canonical_rows('history',rows)).encode()


def verify_export_artifacts(manifest,receipt_path,history_path):
    # Both artifact bytes and canonical content are checked. A hash-shaped string
    # alone is never accepted as proof that an export receipt exists.
    receipt_bytes=Path(receipt_path).read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest()!=manifest['history_export_receipt_sha256']:raise ValueError('history receipt bytes differ')
    receipt=json.loads(receipt_bytes)
    if receipt.get('kind')!='ACTUAL_SNOWFLAKE_HISTORY_EXPORT' or receipt.get('snapshot')!=manifest['history_snapshot_utc'] or receipt.get('relation')!='ANALYTICS.SNAPSHOTS.snap_employer':raise ValueError('history receipt source/snapshot differs')
    source=receipt.get('source',{})
    if not source.get('account') or not source.get('region') or not source.get('user') or not source.get('role'):raise ValueError('observed native export source missing')
    queries=receipt.get('queries',{})
    if set(queries)!={'history','lineage'} or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in queries.values()):raise ValueError('history query identity missing')
    lineage=receipt.get('lineage',{})
    for key in ('registry_sha256','model_sha256'):
        if lineage.get(key)!=manifest[key]:raise ValueError('history receipt lineage differs')
    if lineage.get('acceptance_status')!=manifest['er_acceptance_status']:raise ValueError('history receipt quality lineage differs')
    payload=Path(history_path).read_bytes()
    rows=[json.loads(line) for line in payload.splitlines()]
    observed=content_digest('history',rows)
    if receipt.get('history')!=dict(observed,bytes_sha256=hashlib.sha256(payload).hexdigest()):raise ValueError('history artifact/content differs from receipt')
    if receipt['history']['bytes_sha256']!=manifest['history_sha256']:raise ValueError('history artifact differs from manifest')
    expected=next(r for r in manifest['references'] if r['table']=='employer_history')
    if expected['rows']!=observed['rows'] or expected['sha256']!=receipt['history']['bytes_sha256']:raise ValueError('history reference differs')
    return receipt


def verify_observed_export(reference,receipt):
    # These fields come from new pinned SQL reads, never from config.identity.
    for key in ('source','snapshot','lineage','history','queries'):
        if reference.get(key)!=receipt.get(key):raise ValueError('independent native export observation differs: '+key)
    return True
