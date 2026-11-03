"""Registry metadata only; complete lock delta, including optional platform packages."""
import concurrent.futures
import json
import subprocess
import sys
import urllib.request
import urllib.parse
import io
import tarfile
import hashlib
import base64
from pathlib import Path
import yaml
lock=list(yaml.safe_load_all(Path('web/pnpm-lock.yaml').read_text()))[-1]
base=sys.argv[1] if len(sys.argv)>1 else 'a29eb977701749c8f80bb0f5d8234be7219cda84'
old=list(yaml.safe_load_all(subprocess.check_output(['git','show',base+':web/pnpm-lock.yaml'])))[-1]
added=sorted(set(lock['packages'])-set(old['packages']))
def inspect(key):
 name,version=key.rsplit('@',1)
 with urllib.request.urlopen('https://registry.npmjs.org/'+urllib.parse.quote(name,safe='')+'/'+version) as r:d=json.load(r)
 size=d['dist'].get('unpackedSize',0)
 if not size:
  with urllib.request.urlopen(d['dist']['tarball']) as r: blob=r.read(10000000)
  algorithm,digest=d['dist']['integrity'].split('-',1)
  assert base64.b64encode(hashlib.new(algorithm,blob).digest()).decode()==digest
  with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as t:size=sum(m.size for m in t.getmembers() if m.isfile())
 assert size>0 and d['dist'].get('integrity')
 return {'name':name,'version':version,'bytes':size,'sizeSource':'registry' if d['dist'].get('unpackedSize') else 'integrity-verified in-memory tar members','os':d.get('os'),'cpu':d.get('cpu'),'libc':d.get('libc'),'integrity':d['dist'].get('integrity')}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(inspect,added))
compatible=[r for r in rows if (not r['os'] or 'linux' in r['os']) and (not r['cpu'] or 'x64' in r['cpu']) and (not r['libc'] or 'glibc' in r['libc'])]
d={'metadataOnly':True,'base':base,'packages':rows,'count':len(rows),'allPlatformsUnpacked':sum(r['bytes'] for r in rows),'linuxX64GlibcUnpacked':sum(r['bytes'] for r in compatible)}
Path('web/apps/site/quality-dependency-estimate.json').write_text(json.dumps(d,indent=2)+'\n')
print({k:v for k,v in d.items() if k!='packages'})
