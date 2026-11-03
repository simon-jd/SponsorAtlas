"""Audit existing locked modules before the allowlisted image stream. Installs nothing.
This snapshots current files; it does not re-fetch/verify all upstream tarballs.
"""
import json
import pathlib
import hashlib
import os
root=pathlib.Path(__file__).resolve().parents[4]
modules=root/'semantic/node_modules'
lockpath=root/'semantic/package-lock.json';lock=json.loads(lockpath.read_text());count=0
for rel, expected in lock['packages'].items():
 if not rel:continue
 p=root/'semantic'/rel/'package.json'
 if p.exists():
  actual=json.loads(p.read_text());assert actual['version']==expected['version'],rel;count+=1
 else:assert expected.get('optional'), 'Required installed package absent: '+rel
entries=[];size=0;links=0
for base,dirs,files in os.walk(modules,followlinks=False):
 for name in sorted(dirs+files):
  p=pathlib.Path(base)/name;rel=str(p.relative_to(modules))
  if p.is_symlink():
   assert p.resolve().is_relative_to(modules.resolve()),rel
   entries.append([rel,'link',os.readlink(p)]);links+=1
  elif p.is_file():
   with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
   n=p.stat().st_size;size+=n;entries.append([rel,n,digest])
entries.sort();manifest=json.dumps(entries,separators=(',',':')).encode()
evidence=root/'web/apps/site/evidence';annex=evidence/'quality-annex';annex.mkdir(exist_ok=True)
(annex/'module-files.json').write_bytes(manifest)
with (modules/'@cubejs-backend/native/native/index.node').open('rb') as f:native=hashlib.file_digest(f,'sha256').hexdigest()
assert native=='66b0029ca31686ba843fe1af06f5e215da67b97595033c09914c275ea9f65a8a'
receipt={'installedPackagesComparedToLock':count,'filesAndLinks':len(entries),'symlinks':links,'regularBytes':size,'fileManifestSHA256':hashlib.sha256(manifest).hexdigest(),'lockSHA256':hashlib.sha256(lockpath.read_bytes()).hexdigest(),'nativeSHA256':native,'scope':'Installed version/lock and complete file snapshot; not re-download upstream tarball comparison. Actual ABI proof recorded separately.','mismatches':[]}
(evidence/'quality-docker-module-audit.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
