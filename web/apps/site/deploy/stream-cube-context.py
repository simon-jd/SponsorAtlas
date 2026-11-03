"""Emit a strictly allowlisted context, without a second host module copy.
Usage: python3 deploy/stream-cube-context.py ROOT STAGED_SNAPSHOT | docker build ... -
The controller must separately approve the complete image/runtime peak first.
"""
import sys
import tarfile
import pathlib
import json
import hashlib
root = pathlib.Path(sys.argv[1]).resolve()
snapshot = pathlib.Path(sys.argv[2]).resolve()
modules = root / 'semantic/node_modules'
# Compare every byte/link to the prebuild audit while emitting; reject additions/deletions.
audit = root / 'web/apps/site/evidence/quality-annex/module-files.json'
expected = json.loads(audit.read_text())
receipt = json.loads((root / 'web/apps/site/evidence/quality-docker-module-audit.json').read_text())
assert hashlib.sha256(audit.read_bytes()).hexdigest() == receipt['fileManifestSHA256']
assert hashlib.sha256((root/'semantic/package-lock.json').read_bytes()).hexdigest() == receipt['lockSHA256']
actual_paths = {str(p.relative_to(modules)) for p in modules.rglob('*') if p.is_file() or p.is_symlink()}
assert actual_paths == {entry[0] for entry in expected}, 'Module file set changed'
class HashWriter:
 def __init__(self): self.hash=hashlib.sha256(); self.size=0
 def write(self,data):
  self.hash.update(data); self.size+=len(data); return sys.stdout.buffer.write(data)
writer=HashWriter()
with tarfile.open(fileobj=writer, mode='w|', format=tarfile.PAX_FORMAT) as archive:
 archive.add(root/'web/apps/site/deploy/Cube.Dockerfile', arcname='Dockerfile')
 for entry in expected:
  p = modules/entry[0]
  if entry[1] == 'link':
   assert p.is_symlink() and p.readlink().as_posix() == entry[2] and p.resolve().is_relative_to(modules)
  else:
   assert p.stat().st_size == entry[1]
   with p.open('rb') as f: assert hashlib.file_digest(f, 'sha256').hexdigest() == entry[2]
  archive.add(p, arcname='semantic/node_modules/'+entry[0], recursive=False)
 for p in sorted((root/'semantic').glob('*.cjs')):
  archive.add(p,arcname='semantic/'+p.name)
 for name in ('package.json','package-lock.json','projections.json'):
  archive.add(root/'semantic'/name,arcname='semantic/'+name)
 archive.add(root/'semantic/model',arcname='semantic/model')
 for name in ('lca.parquet','postings.parquet','wages.parquet','export-evidence.json'):
  p=snapshot/name
  assert p.is_file() and not p.is_symlink() and p.stat().st_size < 100000000
  archive.add(p,arcname='snapshot/'+name)

sys.stdout.buffer.flush()
(root/'.tools/task15-docker/context-receipt.json').write_text(json.dumps({'sha256':writer.hash.hexdigest(),'bytes':writer.size,'scope':'Allowlist tar stream; no stored host archive or module copy'},indent=2))
