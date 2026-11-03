"""Official TWB XSD plus cross-reference checks, never native visual verification.

Uses existing libxml2 via ctypes (no new Python dependency). Official Tableau
validator supplies equivalent user/XML namespace stubs for location-less imports.
"""
import ctypes as C
import ctypes.util
import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from snapshot import ROOT,sha

BI=ROOT/'bi'
BASE='https://raw.githubusercontent.com/tableau/tableau-plugin/main/src/plugin/skills/tableau-workbook-authoring/'
SCHEMA_URL=BASE+'resources/schemas/2022_4/twb_2022.4.0.xsd'


def validate_xsd():
    work=BI/'.work';work.mkdir(exist_ok=True)
    original=work/'tableau-2022.4.xsd'
    if not original.exists():
        with urllib.request.urlopen(SCHEMA_URL,timeout=30) as response:data=response.read(400001)
        if len(data)>400000:raise ValueError('Schema size cap')
        original.write_bytes(data)
    # Same unresolved import accommodation as Tableau's official validator.
    namespaces={'user':'http://www.tableausoftware.com/xml/user','xml':'http://www.w3.org/XML/1998/namespace'}
    text=original.read_text()
    for name,ns in namespaces.items():
        body=('<xs:attributeGroup name="UserAttributes-AG"><xs:anyAttribute namespace="##any" processContents="lax"/></xs:attributeGroup>' if name=='user' else '<xs:attribute name="base" type="xs:anyURI"/>')
        stub=work/f'{name}-stub.xsd';stub.write_text(f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="{ns}">{body}</xs:schema>')
        text=re.sub(r'<xs:import namespace="'+re.escape(ns)+r'"\s*/>',f'<xs:import namespace="{ns}" schemaLocation="{stub.name}"/>',text)
    patched=work/'tableau-resolved.xsd';patched.write_text(text)
    lib=C.CDLL(ctypes.util.find_library('xml2'))
    signatures={
        'xmlSchemaNewParserCtxt':([C.c_char_p],C.c_void_p),
        'xmlSchemaParse':([C.c_void_p],C.c_void_p),
        'xmlSchemaNewValidCtxt':([C.c_void_p],C.c_void_p),
        'xmlReadFile':([C.c_char_p,C.c_char_p,C.c_int],C.c_void_p),
        'xmlSchemaValidateDoc':([C.c_void_p,C.c_void_p],C.c_int),
        **{n:([C.c_void_p],None) for n in ['xmlSchemaFreeParserCtxt','xmlSchemaFree','xmlSchemaFreeValidCtxt','xmlFreeDoc']}}
    for name,(args,result) in signatures.items():
        getattr(lib,name).argtypes=args;getattr(lib,name).restype=result
    class Error(C.Structure):
        _fields_=[('domain',C.c_int),('code',C.c_int),('message',C.c_char_p),('level',C.c_int),('file',C.c_char_p),('line',C.c_int)]
    messages=[]
    callback_type=C.CFUNCTYPE(None,C.c_void_p,C.POINTER(Error))
    @callback_type
    def callback(_,error):
        messages.append({'line':error.contents.line,'message':error.contents.message.decode().strip()})
    lib.xmlSchemaSetValidStructuredErrors.argtypes=[C.c_void_p,callback_type,C.c_void_p]
    lib.xmlSchemaSetValidStructuredErrors.restype=None
    parser=lib.xmlSchemaNewParserCtxt(str(patched).encode());schema=lib.xmlSchemaParse(parser)
    if not schema:raise ValueError('Official schema did not compile')
    valid=lib.xmlSchemaNewValidCtxt(schema);document=lib.xmlReadFile(str(BI/'tableau/sponsoratlas.twb').encode(),None,2048)
    if not document:raise ValueError('Workbook XML parse failed')
    try:
        lib.xmlSchemaSetValidStructuredErrors(valid,callback,None)
        result=lib.xmlSchemaValidateDoc(valid,document)
        if result<0 or (result!=0 and not messages):raise ValueError('libxml2 validation could not complete')
    finally:
        lib.xmlFreeDoc(document);lib.xmlSchemaFreeValidCtxt(valid);lib.xmlSchemaFree(schema);lib.xmlSchemaFreeParserCtxt(parser)
    # Upstream validator documents these public-schema vs native-export gaps.
    # Keep warnings visible instead of claiming a strict zero-error XSD pass.
    warnings=[];errors=[]
    for issue in messages:
        message=issue['message']
        gap=("attribute 'source-build' is required but missing" in message or
             ("Missing child element(s)" in message and ('simple-id' in message or 'explain-data' in message)))
        (warnings if gap else errors).append(issue)
    return errors,warnings,sha(original)


def check_references(root):
    sheets={w.get('name'):w for w in root.findall('./worksheets/worksheet')}
    dashboards={d.get('name'):d for d in root.findall('./dashboards/dashboard')}
    for sheet in sheets.values():
        deps=sheet.find('./table/view/datasource-dependencies');fact=deps.get('datasource')
        instances={e.get('name') for e in deps.findall('column-instance')}
        for fil in sheet.findall('./table/view/filter'):
            instance=fil.get('column').removeprefix(f'[{fact}].')
            if instance not in instances:raise ValueError('Dangling filter instance')
            for group in fil.findall('.//groupfilter'):
                if group.get('level') and group.get('level')!=instance:raise ValueError('Filter level differs from declared instance')
        qualified={fil.get('column') for fil in sheet.findall('./table/view/filter')}
        if {e.text for e in sheet.findall('./table/view/slices/column')}!=qualified:raise ValueError('Filter shelf mismatch')
        window=next(w for w in root.findall('./windows/window') if w.get('name')==sheet.get('name'))
        if {c.get('param') for c in window.findall('.//card[@type="filter"]')}!=qualified:raise ValueError('Filter cards missing/invalid')
    actions=root.findall('./actions/action')
    if len(actions)!=4:raise ValueError('Four overview drill/filter actions required')
    expected={'employer','soc','metro','fiscal_year'}
    actual=set()
    for action in actions:
        source=action.find('source');command=action.find('command');params={p.get('name'):p.get('value') for p in command.findall('param')}
        if source.get('worksheet') not in sheets or source.get('dashboard') not in dashboards:raise ValueError('Dangling action source')
        if params['target'] not in dashboards:raise ValueError('Dangling action target')
        if '05 OEWS national 15-1252 survey 2025' not in params['exclude']:raise ValueError('LCA filters must not alter fixed benchmark')
        if action.find('activation').get('auto-clear')!='true':raise ValueError('Selection reset missing')
        field=params['field-captions'];actual.add(field)
        target_sheets=[z.get('name') for z in dashboards[params['target']].findall('./zones/zone') if z.get('name')]
        for name in target_sheets:
            if name in params['exclude'].split(','):continue
            deps=sheets[name].find('./table/view/datasource-dependencies')
            if f'[{field}]' not in {e.get('name') for e in deps.findall('column')}:raise ValueError('Cross-fact filter key absent')
    if actual!=expected:raise ValueError('Wrong filter actions')
    return len(actions)


def check_comparison(root):
    """Verify the four values share controls, rather than parallel private filters."""
    names={f'11 Matched LCA wage p{p} USD' for p in (50,75,90)} | {'12 Matched official annual median','13 Comparison LCA scope','14 Comparison benchmark status'}
    dashboard=root.find('./dashboards/dashboard[@name="SponsorAtlas - matched wage comparison"]')
    if dashboard is None:raise ValueError('Matched comparison dashboard missing')
    zones=dashboard.findall('./zones/zone')
    if {z.get('name') for z in zones if not z.get('type-v2')}!=names:raise ValueError('Comparison values/status not placed together')
    parameters={c.get('name'):c for c in root.findall('./datasources/datasource[@name="Parameters"]/column')}
    expected={'[Comparison SOC]','[Comparison Area]','[Comparison FY]','[Comparison Survey Year]'}
    if set(parameters)!=expected:raise ValueError('Shared comparison parameters missing')
    if {z.get('param') for z in zones if z.get('type-v2')=='paramctrl'}!={'[Parameters].'+p for p in expected}:raise ValueError('Shared parameter controls missing')
    for name in names:
        ws=root.find(f'./worksheets/worksheet[@name="{name}"]')
        if ws is None or ws.findall('./table/view/filter'):raise ValueError('Comparison sheet missing or has drifting local filters')
        deps=ws.find('./table/view/datasource-dependencies[@datasource="Parameters"]')
        if deps is None or {c.get('name') for c in deps.findall('column')}!=expected:raise ValueError('Comparison parameter dependency missing')
        for col in deps.findall('column'):
            original=parameters[col.get('name')]
            if col.attrib!=original.attrib or col.find('calculation').attrib!=original.find('calculation').attrib:raise ValueError('Comparison parameter state differs')
    for fact,year,other_year in [('lca','Comparison FY','Comparison Survey Year'),('wages','Comparison Survey Year','Comparison FY')]:
        ds=root.find(f'./datasources/datasource[@name="{fact}"]')
        match=ds.find('./column[@name="[comparison_match]"]/calculation').get('formula')
        for parameter in ('Comparison SOC','Comparison Area',year):
            if '[Parameters].['+parameter+']' not in match:raise ValueError('Comparison row selection disconnected')
        if other_year in match:raise ValueError('Fiscal and survey years must not cross-filter')
        if '"1:99"' not in match or '"4:"' not in match:raise ValueError('National/metropolitan guard missing')
        if fact=='lca' and '[metro]=MID([Parameters].[Comparison Area],3,5)' not in match:raise ValueError('CBSA not mapped to LCA metro')
        if fact=='wages' and '[area]=[Parameters].[Comparison Area]' not in match:raise ValueError('OEWS published area not matched')
    for p in (50,75,90):
        formula=root.find(f'./datasources/datasource[@name="lca"]/column[@name="[comparison_wage_p{p}]"]/calculation').get('formula')
        if f'PERCENTILE(IF [comparison_match] THEN [annual_wage] END, {p/100})'!=formula:raise ValueError('Comparison percentile bypasses common scope')
    median=root.find('./datasources/datasource[@name="wages"]/column[@name="[comparison_oews_median]"]/calculation').get('formula')
    if 'IF [comparison_estimate_count]=1' not in median or 'IF [comparison_match]' not in median:raise ValueError('Comparison median bypasses scope or uniqueness')
    return {'dashboard':'SponsorAtlas - matched wage comparison','sheets':6,'shared_parameters':4,'native_behavior':'NOT_EXECUTED'}


def main():
    errors,warnings,schema_sha=validate_xsd()
    root=ET.parse(BI/'tableau/sponsoratlas.twb').getroot()
    actions=check_references(root)
    comparison=check_comparison(root)
    package=BI/'tableau/sponsoratlas.twbx'
    extracts=json.loads((BI/'evidence/hyper-extract.json').read_text())['extracts']
    with zipfile.ZipFile(package) as z:
        if z.read('sponsoratlas.twb')!=(BI/'tableau/sponsoratlas.twb').read_bytes():raise ValueError('Package source differs')
        for name,record in extracts.items():
            if hashlib.sha256(z.read(f'Data/{name}.hyper')).hexdigest()!=record['sha256']:raise ValueError('Repack changed extract')
    result={'status':'FAIL' if errors else 'PASS_WITH_UPSTREAM_SCHEMA_WARNINGS' if warnings else 'PASS','scope':'TWB XSD and references only; native interactions/render NOT executed','target_schema':'2022.4','schema_url':SCHEMA_URL,'schema_sha256':schema_sha,'filter_action_reference':BASE+'resources/examples/dashboard-filter-action.json','validator_gap_reference':BASE+'scripts/validate_workbook.py','actions':actions,'comparison':comparison,'errors':errors,'warnings':warnings,'twbx_sha256':sha(package)}
    (BI/'evidence/tableau-source-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return bool(errors)


if __name__=='__main__':raise SystemExit(main())
