"""Validate PBIR against Microsoft's published schemas; not a native render test."""
import hashlib
import json
import urllib.request
from jsonschema import validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7
from snapshot import ROOT, sha

CACHE=ROOT/'bi/.work/schemas'
PREFIX='https://developer.microsoft.com/json-schemas/'
sources={}


def retrieve(uri):
    if not uri.startswith(PREFIX):
        raise ValueError('Only official Microsoft schema retrieval allowed')
    CACHE.mkdir(parents=True,exist_ok=True)
    file=CACHE/(hashlib.sha256(uri.encode()).hexdigest()+'.json')
    if not file.exists():
        with urllib.request.urlopen(uri,timeout=30) as response:
            data=response.read(2_000_001)
        if len(data)>2_000_000:raise ValueError('Schema size cap')
        file.write_bytes(data)
    schema=json.loads(file.read_text())
    schema.setdefault('$id',uri)
    sources[uri]=sha(file)
    return Resource.from_contents(schema,default_specification=DRAFT7)


def main():
    files=list((ROOT/'bi/powerbi/SponsorAtlas.Report').rglob('*.json'))
    files.append(ROOT/'bi/powerbi/SponsorAtlas.Report/definition.pbir')
    files.extend([ROOT/'bi/powerbi/sponsoratlas.pbip',ROOT/'bi/powerbi/SponsorAtlas.Report/.platform',ROOT/'bi/powerbi/SponsorAtlas.SemanticModel/.platform'])
    registry=Registry(retrieve=retrieve)
    for path in files:
        document=json.loads(path.read_text());url=document['$schema']
        schema=retrieve(url).contents
        cls=validators.validator_for(schema)
        cls(schema,registry=registry).validate(document)
    evidence={'status':'PASS','scope':'Published PBIR JSON schemas ONLY; TMDL/M/DAX/native application not executed','files':len(files),'schemas':sources}
    (ROOT/'bi/evidence/pbir-schema.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(f'{len(files)} PBIR files validate; native open/render remains OPEN')


if __name__=='__main__':main()
