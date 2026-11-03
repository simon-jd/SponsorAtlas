"""Registry metadata only; complete lock delta, including optional platform packages."""
import concurrent.futures
import json
import subprocess
import sys
import urllib.request
import urllib.parse
from pathlib import Path
import yaml
lock=list(yaml.safe_load_all(Path('web/pnpm-lock.yaml').read_text()))[-1]
base=sys.argv[1] if len(sys.argv)>1 else 'd93812d1290b8aa03d53ab02a4ceb65d8b917ac4'
old=list(yaml.safe_load_all(subprocess.check_output(['git','show',base+':web/pnpm-lock.yaml'])))[-1]
added=sorted(set(lock['packages'])-set(old['packages']))
def inspect(key):
 name,version=key.rsplit('@',1)
 with urllib.request.urlopen('https://registry.npmjs.org/'+urllib.parse.quote(name,safe='')+'/'+version) as r:d=json.load(r)
 return {'name':name,'version':version,'bytes':d['dist'].get('unpackedSize',0),'os':d.get('os'),'cpu':d.get('cpu'),'libc':d.get('libc'),'integrity':d['dist'].get('integrity')}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(inspect,added))
compatible=[r for r in rows if (not r['os'] or 'linux' in r['os']) and (not r['cpu'] or 'x64' in r['cpu']) and (not r['libc'] or 'glibc' in r['libc'])]
d={'metadataOnly':True,'base':base,'packages':rows,'count':len(rows),'allPlatformsUnpacked':sum(r['bytes'] for r in rows),'linuxX64GlibcUnpacked':sum(r['bytes'] for r in compatible)}
Path('web/apps/site/dependency-estimate.json').write_text(json.dumps(d,indent=2)+'\n')
print({k:v for k,v in d.items() if k!='packages'})
