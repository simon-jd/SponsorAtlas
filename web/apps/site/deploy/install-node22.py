"""Isolated, integrity-checked deployment runtime; no change to shared Node24 tooling."""
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path
version='v22.23.4'
name=f'node-{version}-linux-x64'
expected='1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce'
root=Path('../../.tools/node22').resolve();root.mkdir(parents=True,exist_ok=True)
archive=root/(name+'.tar.xz')
try:
 with urllib.request.urlopen(f'https://nodejs.org/dist/{version}/{name}.tar.xz') as response,archive.open('xb') as out:
  count=0
  while chunk:=response.read(1024*1024):
   count+=len(chunk)
   if count>32000000:raise RuntimeError('Archive quota')
   out.write(chunk)
 assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
 with tarfile.open(archive,'r:xz') as bundle:
  members=bundle.getmembers();size=sum(m.size for m in members)
  assert 0<size<=200000000
  for m in members:
   assert (m.name.startswith(name+'/') or (m.name==name and m.isdir())) and not m.name.startswith('/') and '..' not in Path(m.name).parts
   assert m.isfile() or m.isdir() or m.issym()
   if m.issym():assert not Path(m.linkname).is_absolute() and (root/m.name).parent.joinpath(m.linkname).resolve().is_relative_to(root/name)
  bundle.extractall(root,filter='data')
 Path('evidence/quality-node22-install.json').write_text(json.dumps({'version':version,'url':f'https://nodejs.org/dist/{version}/{name}.tar.xz','sha256':expected,'tarBytes':count,'unpackedBytes':size,'capBytes':200000000,'root':str(root),'scope':'LOCAL_DEPLOYMENT_COMPATIBILITY_ONLY'},indent=2)+'\n')
finally:archive.unlink(missing_ok=True)
