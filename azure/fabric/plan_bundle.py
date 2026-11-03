"""Prepare a secret-free immutable bundle manifest after native inputs exist.

Does not upload/copy archives or open a database. Hashes only the explicitly
provided reference/export files; JSONL hashes/counts come from reviewed provenance.
This command itself requires the separately approved source/export resource plan.
"""
import argparse
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from azure.fabric.archive import sha_file,schema_digest,validate_manifest
from azure.fabric.warehouse import sql_digest
from azure.fabric.content import verify_export_artifacts

ROOT=Path(__file__).resolve().parents[2]


def code_zip():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name in ('__init__.py','archive.py','contract.py','content.py','content-schema.json'):
            info=zipfile.ZipInfo('sponsoratlas_port/'+name,date_time=(2020,1,1,0,0,0))
            archive.writestr(info,b'' if name=='__init__.py' else (ROOT/'azure/fabric'/name).read_bytes())
    return buffer.getvalue()


def prepare(reference_spec,output):
    spec=json.loads(Path(reference_spec).read_text())
    receipt_path=Path(spec['history_export_receipt_file']);receipt=json.loads(receipt_path.read_text())
    manifest={k:receipt['lineage'][k] for k in ('registry_sha256','model_sha256')}
    manifest.update(history_snapshot_utc=receipt['snapshot'],history_export_receipt_sha256=sha_file(receipt_path),history_export_receipt_path='receipts/'+sha_file(receipt_path)+'.json',er_acceptance_status=receipt['lineage']['acceptance_status'],er_quality_disclosure=spec['er_quality_disclosure'])
    manifest.update(kind='OFFICIAL_ARCHIVE_SUBJECT_PORT',scope='LCA_FY2024_Q4+FY2025_Q4',model_version='sponsoratlas-lca-port-v1',history_semantics='CAPTURE_TIME_UTC',archives=[],references=[])
    code=code_zip()
    manifest.update(code_sha256=hashlib.sha256(code).hexdigest(),warehouse_sql_sha256=sql_digest())
    uploads=[dict(local_path=str(receipt_path),blob_path=manifest['history_export_receipt_path'],sha256=manifest['history_export_receipt_sha256'])]
    for path in sorted((ROOT/'data/raw/lca').glob('*/*.provenance.json')):
        source=json.loads(path.read_text())
        name=Path(source['path']).name;target='raw/'+source['release']+'/'+name
        manifest['archives'].append(dict(path=target,release=source['release'],original_sha256=source['sha256'],sha256=source['derived_sha256'],schema_sha256=schema_digest(source['original_headers']),rows=source['source_rows'],url=source['url']))
        uploads.append(dict(local_path=source['path'],blob_path=target,sha256=source['derived_sha256']))
    for ref in spec['references']:
        source=Path(ref['local_path']);fmt=ref['format'];count=0;columns=None
        with source.open() as stream:
            iterator=csv.DictReader(stream) if fmt=='csv' else (json.loads(line) for line in stream)
            for row in iterator:
                count+=1
                if columns is None:columns=set(row)
                if set(row)!=columns:raise ValueError('reference schema drift')
        sha=sha_file(source);target='references/'+ref['table']+'_'+sha+'.'+fmt
        manifest['references'].append(dict(table=ref['table'],format=fmt,path=target,rows=count,sha256=sha,schema_sha256=schema_digest(columns or [])))
        uploads.append(dict(local_path=str(source),blob_path=target,sha256=sha))
        if ref['table']=='employer_history':manifest['history_sha256']=sha
        if ref['table']=='employer_xref':manifest['xref_sha256']=sha
    mapping=ROOT/'dbt/seeds/lca_column_map.csv'
    manifest.update(mapping_path='references/lca_column_map.csv',mapping_sha256=sha_file(mapping))
    uploads.append(dict(local_path=str(mapping),blob_path=manifest['mapping_path'],sha256=manifest['mapping_sha256']))
    validate_manifest(manifest)
    history=next(r for r in spec['references'] if r['table']=='employer_history')
    verify_export_artifacts(manifest,receipt_path,history['local_path'])
    output=Path(output)
    if output.exists():raise ValueError('refuse overwriting immutable bundle plan')
    output.mkdir(parents=True)
    (output/'receipts').mkdir()
    (output/manifest['history_export_receipt_path']).write_bytes(receipt_path.read_bytes())
    (output/'azure-port-code.zip').write_bytes(code)
    uploads.append(dict(local_path=str(output/'azure-port-code.zip'),blob_path='azure-port-code.zip',sha256=manifest['code_sha256']))
    manifest_path=output/'manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    digest=sha_file(manifest_path)
    uploads.append(dict(local_path=str(manifest_path),blob_path='manifest.json',sha256=digest))
    files=[dict(folder='' if Path(u['blob_path']).parent==Path('.') else str(Path(u['blob_path']).parent),file=Path(u['blob_path']).name) for u in uploads]
    (output/'uploads.json').write_text(json.dumps(uploads,indent=2)+'\n')
    (output/'pipeline-parameters.json').write_text(json.dumps(dict(manifestSha256=digest,files=files),indent=2)+'\n')
    return {'kind':'UNEXECUTED_NATIVE_BUNDLE_PLAN','manifest_sha256':digest,'files':len(files)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference-spec',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(prepare(a.reference_spec,a.output)))
