"""Finite local image proof. Requires separately approved image/disk budget.
Only the explicitly task15-labeled image/container is operated; secrets never logged.
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[4]
E=ROOT/'web/apps/site/evidence'
IMAGE=(ROOT/'.tools/task15-docker/image-id').read_text().strip()
NAME='sponsoratlas-task15-verify'
PREFIX=os.environ.get('QUALITY_IMAGE_RECEIPT','quality-fix1-docker')
def cmd(*args):return subprocess.check_output(['docker',*args],text=True,timeout=30)
image=json.loads(cmd('image','inspect',IMAGE))[0]
assert image['Config']['Labels']['org.sponsoratlas.task']=='15'
assert image['Config']['Labels']['org.sponsoratlas.owner']=='codex-sponsoratlas'
secret=secrets.token_hex(32)
fd,envpath=tempfile.mkstemp(prefix='task15-docker-',dir=ROOT/'.tools/task15-docker')
os.write(fd,('CUBEJS_API_SECRET='+secret+'\n').encode());os.close(fd)
b64=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
head=b64(b'{"alg":"HS256","typ":"JWT"}');body=b64(json.dumps({'iat':int(time.time()),'exp':int(time.time())+180}).encode());unsigned=head+'.'+body
token=unsigned+'.'+b64(hmac.new(secret.encode(),unsigned.encode(),hashlib.sha256).digest())
def http(path,auth=False,data=None):
 req=urllib.request.Request('http://127.0.0.1:4415'+path,data=json.dumps(data).encode() if data else None,headers={'Content-Type':'application/json',**({'Authorization':token} if auth else {})})
 try:
  with urllib.request.urlopen(req,timeout=8) as res:return res.status,json.load(res)
 except urllib.error.HTTPError as err:return err.code,json.loads(err.read())
record={'image':IMAGE,'sourceRevision':image['Config']['Labels']['org.opencontainers.image.revision'],'host':'127.0.0.1:4415','memoryLimitBytes':2147483648,'cpuQuota':1,'pidsLimit':128,'writableLayerLimitMonitoredBytes':256000000,'sourceReadOnly':'root-owned0444 source entries and immutable ancestors; sibling /app/work only writable','scope':'Local actual public projection/native ABI and transport only; no hosted/scale-to-zero proof'}
cid=None
try:
 cid=cmd('run','-d','--name',NAME,'--label','org.sponsoratlas.project=sponsoratlas','--label','org.sponsoratlas.owner=codex-sponsoratlas','--label','org.sponsoratlas.task=15','--memory=2g','--memory-swap=2g','--cpus=1','--pids-limit=128','--cap-drop=ALL','--security-opt=no-new-privileges','--publish','127.0.0.1:4415:4000','--env-file',envpath,IMAGE).strip()
 ownedpath=ROOT/'.tools/task15-docker-owned-containers.json'
 owned=json.loads(ownedpath.read_text()) if ownedpath.exists() else {'containers':[]}
 owned['containers'].append({'id':cid,'name':NAME,'image':IMAGE,'retained':True,'removed':False,'beforeExisting':False})
 ownedpath.write_text(json.dumps(owned,indent=2))
 os.unlink(envpath)
 start=time.monotonic();ready=False;peakrw=0
 while time.monotonic()-start<75:
  state=json.loads(cmd('inspect','--size',cid))[0];peakrw=max(peakrw,state.get('SizeRw',0));assert peakrw<256000000
  if not state['State']['Running']:raise RuntimeError('Container exited before readiness')
  try:
   status,_=http('/cubejs-api/v1/meta')
   if status==403:ready=True;break
  except (OSError,urllib.error.URLError):pass
  time.sleep(.5)
 assert ready,'Readiness75s exceeded'
 record['startupSeconds']=round(time.monotonic()-start,3);record['noJWTStatus']=status
 status,meta=http('/cubejs-api/v1/meta',True);assert status==200 and meta['cubes'];record['validJWTMetaStatus']=status
 result=None
 for attempt in range(20):
  status,result=http('/cubejs-api/v1/load',True,{'query':{'measures':['lca_metrics.case_count']}})
  if status==200 and 'data' in result:break
  assert result.get('error')=='Continue wait',result
  time.sleep(.5)
 assert status==200 and int(result['data'][0]['lca_metrics.case_count'])>0
 record['validJWTLoadStatus']=status;record['caseCount']=result['data'][0]['lca_metrics.case_count']
 record['runtime']=json.loads(cmd('exec',cid,'node','-e','console.log(JSON.stringify({node:process.version,platform:process.platform,arch:process.arch,uid:process.getuid(),glibc:process.report.getReport().header.glibcVersionRuntime}))'))
 assert record['runtime']['uid']==1000 and record['runtime']['node']=='v24.22.1'
 record['driverPreflight']=json.loads(cmd('exec',cid,'cat','/app/work/driver-evidence.json'))
 assert 'EACCES' in record['driverPreflight']['source_os_write']
 assert 'disabled' in record['driverPreflight']['external_read'].lower()
 record['namespaceProof']=json.loads(cmd('exec',cid,'node','/app/semantic/namespace-proof.cjs'))
 record['resourceSample']=json.loads(cmd('exec',cid,'cat','/app/work/resource.json'))
 record['dockerStatsSample']=json.loads(cmd('stats','--no-stream','--format','{{json .}}',cid))
 record['sourceFiles']=json.loads(cmd('exec',cid,'node','-e',"const fs=require('fs'),c=require('crypto');console.log(JSON.stringify(['lca','postings','wages'].map(n=>{const p='/app/snapshot/'+n+'.parquet',s=fs.statSync(p);return {name:n,uid:s.uid,mode:s.mode&511,size:s.size,sha256:c.createHash('sha256').update(fs.readFileSync(p)).digest('hex')}})))"))
 state=json.loads(cmd('inspect','--size',cid))[0];peakrw=max(peakrw,state.get('SizeRw',0));assert peakrw<256000000
 record['peakObservedSizeRwBytes']=peakrw
 record['hostConfig']={k:state['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus','PidsLimit','CapDrop','SecurityOpt','PortBindings')}
 record['passed']=True
finally:
 if os.path.exists(envpath):os.unlink(envpath)
 if cid:
  cmd('stop','--time','10',cid)
  state=json.loads(cmd('inspect','--size',cid))[0]
  record['exit']={k:state['State'][k] for k in ('ExitCode','OOMKilled','Running','Error')}
  record['finalSizeRwBytes']=state.get('SizeRw',0)
  record['finalRwWithinCap']=record['finalSizeRwBytes']<256000000
  (E/(PREFIX+'-container.log')).write_text(subprocess.check_output(['docker','logs',cid],text=True,stderr=subprocess.STDOUT,timeout=10))
  cmd('rm',cid)
  owned['containers'][-1].update(retained=False,removed=True)
  ownedpath.write_text(json.dumps(owned,indent=2))
 (E/(PREFIX+'-runtime.json')).write_text(json.dumps(record,indent=2))
assert record['exit']['ExitCode']==0 and not record['exit']['OOMKilled'] and record['finalRwWithinCap']
print(json.dumps({k:record[k] for k in ('passed','caseCount','runtime','startupSeconds','peakObservedSizeRwBytes','exit')}))
