import json
import urllib.request
import urllib.parse
import concurrent.futures
from pathlib import Path
lock=json.loads(Path('web/package-lock.json').read_text())
packages={ (d.get('name',p.split('node_modules/')[-1]),d['version']) for p,d in lock['packages'].items() if 'node_modules/' in p and 'version' in d}
packages.add(('pnpm','12.15.0'))
pnpm_metadata=json.load(urllib.request.urlopen('https://registry.npmjs.org/pnpm/12.15.0'))
packages.update(pnpm_metadata['optionalDependencies'].items())
def inspect(p):
 name,version=p
 with urllib.request.urlopen('https://registry.npmjs.org/'+urllib.parse.quote(name,safe='')+'/'+version) as r: data=json.load(r)
 return {'name':name,'version':version,'unpackedBytes':data.get('dist',{}).get('unpackedSize',0),'integrity':data.get('dist',{}).get('integrity'),'os':data.get('os'),'cpu':data.get('cpu')}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: rows=list(pool.map(inspect,sorted(packages)))
result={'packages':rows,'count':len(rows),'allPlatformsUnpackedBytes':sum(p['unpackedBytes'] for p in rows),'linuxX64UnpackedBytes':sum(p['unpackedBytes'] for p in rows if (not p['os'] or 'linux' in p['os']) and (not p['cpu'] or 'x64' in p['cpu'])),'missingSizes':[p['name'] for p in rows if not p['unpackedBytes']]}
Path('web/graph-estimate.json').write_text(json.dumps(result,indent=2)+'\n')
print({k:v for k,v in result.items() if k!='packages'})
