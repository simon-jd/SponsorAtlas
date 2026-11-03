"""Isolated local Node22 payload smoke using ONLY the already-retained base image.
No repository/projection mount, image build/pull, credential output or hosted claim.
"""
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[4];E=ROOT/'web/apps/site/evidence';ARTIFACT=ROOT/'web/apps/site/deploy/artifact'
BASE='sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae'
NODE=ROOT/'web/.tools/node22/node-v22.23.4-linux-x64/bin/node';NAME='sponsoratlas-task15-artifact-fix1'
def docker(*args):return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.STDOUT,timeout=20)
# Exact retained image must already exist; --pull=never below prohibits downloads.
image=json.loads(docker('image','inspect',BASE))[0];assert image['Id']==BASE
for port in (3100,4100):
 s=socket.socket();s.bind(('127.0.0.1',port));s.close()
secret=secrets.token_hex(32);env={**os.environ,'CUBEJS_API_SECRET':secret,'PORT':'4100'}
for key in ('SEMANTIC_CONTAINER','SEMANTIC_BIND_HOST','SEMANTIC_SNAPSHOT'):env.pop(key,None)
log=(E/'quality-fix1-artifact-cube.log').open('w');cube=subprocess.Popen(['bash',str(ROOT/'semantic/run.sh'),'serve','parquet','actual'],env=env,stdout=log,stderr=subprocess.STDOUT)
def get(url):
 try:
  with urllib.request.urlopen(url,timeout=10) as response:return response.status,response.read()
 except urllib.error.HTTPError as error:return error.code,error.read()
def ready(url):
 for _ in range(150):
  try:
   status,_=get(url)
   if status<500:return
  except OSError:pass
  if cube.poll() is not None:raise RuntimeError('Cube exited')
  time.sleep(.1)
 raise RuntimeError('Readiness exceeded')
fd,envfile=tempfile.mkstemp(prefix='artifact-env-',dir=ROOT/'.tools/task15-docker');os.write(fd,('CUBEJS_API_SECRET='+secret+'\nCUBE_REST_URL=http://127.0.0.1:4100/cubejs-api/v1/load\nHOSTNAME=127.0.0.1\nPORT=3100\nNODE_ENV=production\n').encode());os.close(fd)
record={'scope':'ISOLATED_ACTUAL_ARTIFACT_LOCAL_NOT_HOSTED','baseImage':BASE,'nodeBinary':'existing official Node22.23.4','hostProjectionMounted':False,'tmpfsBytes':{'/tmp':67108864,'/app/.next/cache':33554432},'memoryLimitBytes':2147483648,'CPU':1,'pidsLimit':128,'routes':[]};cid=None
try:
 ready('http://127.0.0.1:4100/cubejs-api/v1/meta');(ARTIFACT/'.next/cache').mkdir(exist_ok=True)
 cid=docker('run','-d','--pull=never','--name',NAME,'--label','org.sponsoratlas.project=sponsoratlas','--label','org.sponsoratlas.owner=codex-sponsoratlas','--label','org.sponsoratlas.task=15','--read-only','--user','1000:1000','--network=host','--memory=2g','--memory-swap=2g','--cpus=1','--pids-limit=128','--cap-drop=ALL','--security-opt=no-new-privileges','--mount',f'type=bind,src={ARTIFACT},dst=/app,readonly','--mount',f'type=bind,src={NODE},dst=/usr/local/bin/node,readonly','--tmpfs','/tmp:rw,nosuid,nodev,size=67108864,mode=1777','--tmpfs','/app/.next/cache:rw,nosuid,nodev,size=33554432,uid=1000,gid=1000,mode=700','--workdir','/app','--env-file',envfile,BASE,'node','server.js').strip();os.unlink(envfile)
 ownedpath=ROOT/'.tools/task15-docker-owned-containers.json';owned=json.loads(ownedpath.read_text());owned['containers'].append({'id':cid,'name':NAME,'image':BASE,'role':'fix1-isolated-artifact','retained':True,'removed':False,'beforeExisting':False});ownedpath.write_text(json.dumps(owned,indent=2))
 ready('http://127.0.0.1:3100/en')
 for locale in ('en','zh'):
  status,body=get(f'http://127.0.0.1:3100/{locale}');html=body.decode();assert status==200;found=re.search(r'data-employer-link="([^"]+)"',html);assert found
  for route in (f'/{locale}',f'/{locale}/employers/{found[1]}',f'/{locale}/compare?ids={found[1]}',f'/{locale}/map'):
   status,body=get('http://127.0.0.1:3100'+route);assert status==200;assert b'CONTROLLED_COORDINATE_FIXTURE' not in body
   record['routes'].append({'path':route,'status':status,'bytes':len(body)})
 status,body=get('http://127.0.0.1:3100/api/viz?resolution=3');data=json.loads(body);assert status==200 and data['coverage']['total']==238891
 ancestry=json.loads((ARTIFACT/'deployment-data/ancestry.json').read_text());assert data['provenance']['version']==ancestry['snapshot'];assert data['provenance']['projection']==ancestry['projectionSHA256'];record['map']={'status':status,'total':data['coverage']['total'],'provenance':data['provenance']}
 record['isolation']=json.loads(docker('exec',cid,'node','-e',"const fs=require('fs');let error;try{fs.readFileSync('/home/postedism/Desktop/SponsorAtlas/.tools/viz/actual/manifest.json');throw Error('Host source visible')}catch(e){if(e.code!=='ENOENT')throw e;error=e.code}console.log(JSON.stringify({hostProjectionRead:error,node:process.version,uid:process.getuid(),cwd:process.cwd()}))"))
 assert record['isolation']['node']=='v22.23.4'
 state=json.loads(docker('inspect','--size',cid))[0];record['mounts']=[{k:x[k] for k in ('Type','Source','Destination','RW')} for x in state['Mounts']];record['readOnlyRoot']=state['HostConfig']['ReadonlyRootfs'];assert record['readOnlyRoot'] and len([x for x in state['Mounts'] if x['Type']=='bind'])==2
 record['dockerStats']=json.loads(docker('stats','--no-stream','--format','{{json .}}',cid));record['passed']=True
finally:
 if os.path.exists(envfile):os.unlink(envfile)
 if cid:
  docker('stop','--time','10',cid);state=json.loads(docker('inspect','--size',cid))[0];record['exit']={k:state['State'][k] for k in ('ExitCode','OOMKilled','Running')};record['sizeRwBytes']=state.get('SizeRw',0)
  (E/'quality-fix1-artifact-container.log').write_text(docker('logs',cid));docker('rm',cid);owned['containers'][-1].update(retained=False,removed=True);ownedpath.write_text(json.dumps(owned,indent=2))
 cube.terminate()
 try:cube.wait(timeout=10)
 except subprocess.TimeoutExpired:cube.kill();cube.wait(timeout=5)
 log.close();record['cubeExit']=cube.returncode;(E/'quality-fix1-artifact-runtime.json').write_text(json.dumps(record,indent=2))
assert record['passed'] and not record['exit']['OOMKilled'] and record['cubeExit']==0
print(json.dumps({'passed':True,'routes':len(record['routes']),'map':record['map'],'isolation':record['isolation'],'exit':record['exit']}))
