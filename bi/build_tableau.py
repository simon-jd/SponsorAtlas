"""Build genuine Hyper extracts and a source-authored TWB/TWBX.

Hyper open/SQL verification is NOT Tableau worksheet calculation/render verification.
Run with PYTHONPATH=bi/.work/hyper-sdk using the hash-verified isolated SDK.
"""
import copy
import argparse
import datetime
import decimal
import json
import os
import resource
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from tableauhyperapi import Connection, CreateMode, HyperProcess, Inserter, SqlType, TableDefinition, TableName, Telemetry
import tableauhyperapi
import cffi

from snapshot import ROOT, connect_snapshot, sha

BI = ROOT / 'bi'
OUT = BI / 'tableau'
DISCLAIMER = 'LCA applications are not approvals and do not guarantee sponsorship. LCA 申请不等于获批，也不代表这个岗位一定提供担保。'
CALCS = {
    'lca': {
        'case_count': 'COUNT([case_number])',
        'certified_rate': 'SUM(IF [is_certified] THEN 1 ELSE 0 END) / COUNT([case_number])',
        **{f'wage_p{p}': f'PERCENTILE([annual_wage], {p/100})' for p in (50,75,90)},
    },
    'postings': {'active_posting_days':'SUM(IF [is_active] THEN 1 ELSE 0 END)'},
    'wages': {
        'oews_median':'IF COUNT([soc]) = 1 THEN MAX([annual_median_wage]) END',
        'estimate_count':'COUNT([soc])',
        'reconcile_oews_median': 'IF SUM(IF [soc]="15-1252" AND [area]="1:99" AND [survey_year]=2025 THEN 1 ELSE 0 END)=1 THEN MAX(IF [soc]="15-1252" AND [area]="1:99" AND [survey_year]=2025 THEN [annual_median_wage] END) END',
    },
}
# Workbook-wide parameters deliberately replace independent comparison filters.
# They select rows inside each aggregate; unrelated overview actions cannot drift them.
PARAMETERS = {
    'Comparison SOC': ('string', '15-1252', 'Shared detailed SOC (e.g. 15-1252)'),
    'Comparison Area': ('string', '1:99', 'Shared area: 1:99 national or 4:CBSA (e.g. 4:35620)'),
    'Comparison FY': ('integer', 0, 'LCA fiscal year (0 = all archived releases)'),
    'Comparison Survey Year': ('integer', 2025, 'OEWS survey year (independent of LCA FY)'),
}
AREA_SUPPORTED = '([Parameters].[Comparison Area]="1:99" OR (LEFT([Parameters].[Comparison Area],2)="4:" AND LEN([Parameters].[Comparison Area])=7))'
LCA_MATCH = '[soc]=[Parameters].[Comparison SOC] AND ' + AREA_SUPPORTED + ' AND ([Parameters].[Comparison Area]="1:99" OR [metro]=MID([Parameters].[Comparison Area],3,5)) AND ([Parameters].[Comparison FY]=0 OR [fiscal_year]=[Parameters].[Comparison FY])'
OEWS_MATCH = '[soc]=[Parameters].[Comparison SOC] AND ' + AREA_SUPPORTED + ' AND [area]=[Parameters].[Comparison Area] AND [survey_year]=[Parameters].[Comparison Survey Year]'
CALCS['lca'].update({
    'comparison_match': LCA_MATCH,
    **{f'comparison_wage_p{p}': f'PERCENTILE(IF [comparison_match] THEN [annual_wage] END, {p/100})' for p in (50,75,90)},
    'comparison_scope': '"SOC " + [Parameters].[Comparison SOC] + " | Area " + [Parameters].[Comparison Area] + " | LCA FY " + (IF [Parameters].[Comparison FY]=0 THEN "all archived" ELSE STR([Parameters].[Comparison FY]) END) + " | " + (IF NOT ' + AREA_SUPPORTED + ' THEN "Not comparable: no LCA mapping for state/nonmetropolitan/unknown area" ELSEIF COUNT(IF [comparison_match] THEN [annual_wage] END)=0 THEN "No matched non-null LCA wages" ELSE "Matched national/CBSA LCA wages" END)',
})
CALCS['wages'].update({
    'comparison_match': OEWS_MATCH,
    'comparison_estimate_count': 'SUM(IF [comparison_match] THEN 1 ELSE 0 END)',
    'comparison_oews_median': 'IF [comparison_estimate_count]=1 THEN MAX(IF [comparison_match] THEN [annual_median_wage] END) END',
    'comparison_status': '"SOC " + [Parameters].[Comparison SOC] + " | Area " + [Parameters].[Comparison Area] + " | OEWS survey " + STR([Parameters].[Comparison Survey Year]) + " | " + (IF NOT ' + AREA_SUPPORTED + ' THEN "Not comparable: unsupported area" ELSEIF [comparison_estimate_count]=0 THEN "No matching published estimate" ELSEIF [comparison_estimate_count]<>1 THEN "Multiple estimates: benchmark unavailable" ELSEIF ISNULL([comparison_oews_median]) THEN "Suppressed/top-coded: benchmark unavailable" ELSE "One official annual median; survey year is not LCA FY" END)',
})
COMPARISON_DASHBOARD = 'SponsorAtlas - matched wage comparison'
COMPARISON_SHEETS = [f'11 Matched LCA wage p{p} USD' for p in (50,75,90)] + ['12 Matched official annual median','13 Comparison LCA scope','14 Comparison benchmark status']

TYPE = {'VARCHAR':('string',SqlType.text), 'BOOLEAN':('boolean',SqlType.bool),
        'DOUBLE':('real',SqlType.double), 'INTEGER':('integer',SqlType.int),
        'BIGINT':('integer',SqlType.big_int), 'DATE':('date',SqlType.date),
        'DECIMAL(24,2)':('real',lambda: SqlType.numeric(24,2)),
        'DECIMAL(18,2)':('real',lambda: SqlType.numeric(18,2))}


def sub(parent, tag, text=None, **attrs):
    item = ET.SubElement(parent,tag,{k:str(v) for k,v in attrs.items()})
    item.text = text
    return item


def workbook(schemas):
    root = ET.Element('workbook', {'version':'18.1','source-platform':'win'})
    sub(sub(root,'document-format-change-manifest'),'SheetIdentifierTracking')
    sources=sub(root,'datasources')
    parameters=sub(sources,'datasource',name='Parameters',caption='Shared wage comparison controls',inline='true',version='18.1',hasconnection='false')
    for name,(typ,value,caption) in PARAMETERS.items():
        literal='"'+value+'"' if typ=='string' else str(value)
        node=sub(parameters,'column',name='['+name+']',caption=caption,datatype=typ,role='measure',type='nominal' if typ=='string' else 'quantitative',value=literal,**{'param-domain-type':'any'})
        sub(node,'calculation',**{'class':'tableau','formula':literal})
    columns={}
    for fact, schema in schemas.items():
        ds=sub(sources,'datasource',name=fact,caption='SponsorAtlas '+fact,inline='true',version='18.1')
        conn=sub(ds,'connection',**{'class':'hyper','dbname':f'Data/{fact}.hyper','schema':'Extract','access_mode':'readonly','authentication':'auth-none'})
        sub(conn,'relation',name='Extract',table='[Extract].[Extract]',type='table')
        columns[fact]={}
        for col,dtype in schema:
            typ=TYPE[dtype][0]
            role='measure' if col in ('annual_wage','annual_median_wage') else 'dimension'
            node=sub(ds,'column',name=f'[{col}]',datatype=typ,role=role,type='quantitative' if role=='measure' else 'nominal')
            columns[fact][col]=node
        for name, formula in CALCS[fact].items():
            typ='boolean' if name=='comparison_match' else 'string' if name in ('comparison_scope','comparison_status') else 'real'
            node=sub(ds,'column',name=f'[{name}]',datatype=typ,role='dimension' if typ=='boolean' else 'measure',type='nominal' if typ!='real' else 'quantitative',caption=name.replace('_',' '))
            sub(node,'calculation',**{'class':'tableau','formula':formula})
            columns[fact][name]=node
    actions=sub(root,'actions')
    worksheets=sub(root,'worksheets')
    sheets=[]
    sheet_controls={}
    def sheet(name,fact,metric,dimension=None,filters=None,comparison=False):
        sheets.append(name)
        ws=sub(worksheets,'worksheet',name=name)
        layout=sub(ws,'layout-options'); title=sub(layout,'title'); formatted=sub(title,'formatted-text')
        sub(formatted,'run',name)
        table=sub(ws,'table');view=sub(table,'view');dss=sub(view,'datasources');sub(dss,'datasource',name=fact)
        if comparison:sub(dss,'datasource',name='Parameters')
        deps=sub(view,'datasource-dependencies',datasource=fact)
        for field_name,node in columns[fact].items():
            if not comparison and field_name.startswith('comparison_'):continue
            deps.append(copy.deepcopy(node))
        metric_type=columns[fact][metric].get('type')
        mi=f'[usr:{metric}:{"qk" if metric_type=="quantitative" else "nk"}]'
        sub(deps,'column-instance',column=f'[{metric}]',derivation='User',name=mi,pivot='key',type=metric_type)
        if dimension:
            di=f'[none:{dimension}:nk]'
            sub(deps,'column-instance',column=f'[{dimension}]',derivation='None',name=di,pivot='key',type='nominal')
        fields={'lca':['employer','soc','metro','fiscal_year'],'postings':['employer','soc','metro','fiscal_year','observation_date'],'wages':['soc','area','survey_year']}[fact]
        # Leave the fixed reconciliation benchmark free of interactive filters.
        if metric=='reconcile_oews_median' or comparison:fields=[]
        sheet_controls[name]=[f'[{fact}].[none:{field}:nk]' for field in fields]
        for field in fields:
            fi=f'[none:{field}:nk]'
            if dimension!=field:sub(deps,'column-instance',column=f'[{field}]',derivation='None',name=fi,pivot='key',type='nominal')
            fil=sub(view,'filter',**{'class':'categorical','column':f'[{fact}].{fi}'})
            if field in (filters or {}):
                value=filters[field]
                sub(fil,'groupfilter',function='member',level=fi,member=f'"{value}"' if isinstance(value,str) else str(value))
            else:sub(fil,'groupfilter',function='level-members',level=fi)
        if comparison:
            parameter_deps=sub(view,'datasource-dependencies',datasource='Parameters')
            for node in parameters.findall('column'):parameter_deps.append(copy.deepcopy(node))
        if fields:
            slices=sub(view,'slices')
            for qualified in sheet_controls[name]:sub(slices,'column',qualified)
        sub(view,'aggregation',value='true');sub(table,'style')
        pane=sub(sub(table,'panes'),'pane');sub(sub(pane,'view'),'breakdown',value='auto')
        sub(pane,'mark',**{'class':'Bar' if dimension else 'Text'})
        if not dimension:
            sub(sub(pane,'encodings'),'text',column=f'[{fact}].{mi}')
        sub(table,'rows',f'[{fact}].{di}' if dimension else None)
        sub(table,'cols',f'[{fact}].{mi}' if dimension else None)
        sub(ws,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/worksheet/'+name)).upper()+'}')
    sheet('01 Latest LCA cases','lca','case_count')
    sheet('02 Certified rate','lca','certified_rate')
    sheet('03 Annual wage p75 USD','lca','wage_p75')
    sheet('04 Active observed posting-days','postings','active_posting_days')
    sheet('05 OEWS national 15-1252 survey 2025','wages','reconcile_oews_median')
    sheet('06 Cases by employer - provisional ER','lca','case_count','employer')
    sheet('07 Cases by detailed SOC','lca','case_count','soc')
    sheet('08 Cases by metropolitan CBSA','lca','case_count','metro')
    sheet('09 Archived release fiscal year','lca','case_count','fiscal_year')
    sheet('10 UTC observations - one date is not a trend','postings','active_posting_days','observation_date')
    for p in (50,75,90):
        sheet(f'11 Matched LCA wage p{p} USD','lca',f'comparison_wage_p{p}',comparison=True)
    sheet('12 Matched official annual median','wages','comparison_oews_median',comparison=True)
    sheet('13 Comparison LCA scope','lca','comparison_scope',comparison=True)
    sheet('14 Comparison benchmark status','wages','comparison_status',comparison=True)
    # Official Tableau authoring example: selected-field on-select filters.
    # Public keys align across LCA/postings; never send FY/employer to OEWS.
    dashboard_name='SponsorAtlas - applications and observed jobs'
    for index,(source_index,field) in enumerate([(5,'employer'),(6,'soc'),(7,'metro'),(8,'fiscal_year')],1):
        action=sub(actions,'action',name=f'[Action{index}]',caption=f'Filter overview by {field}; clear selection to reset')
        sub(action,'activation',**{'auto-clear':'true','type':'on-select'})
        sub(action,'source',dashboard=dashboard_name,type='sheet',worksheet=sheets[source_index])
        command=sub(action,'command',command='tsc:tsl-filter')
        sub(command,'param',name='exclude',value=','.join([sheets[source_index],sheets[4]]))
        sub(command,'param',name='field-captions',value=field)
        sub(command,'param',name='target',value=dashboard_name)
    dashboards=sub(root,'dashboards')
    dashboard=sub(dashboards,'dashboard',name=dashboard_name)
    sub(dashboard,'style');sub(dashboard,'size',maxheight=1000,maxwidth=1440,minheight=1000,minwidth=1440)
    zones=sub(dashboard,'zones')
    for i,name in enumerate(sheets[:10]):
        if i<5:x,y,w,h=i*20000,9000,20000,14000
        elif i<8:x,y,w,h=(i-5)*33333,25000,33333,33000
        else:x,y,w,h=(i-8)*50000,60000,50000,26000
        sub(zones,'zone',id=i+1,name=name,x=x,y=y,w=w,h=h)
    for i,(y,text) in enumerate([(0,'SponsorAtlas | Click employer, SOC, metro or FY marks to filter; clear selection to reset.'),(88000,'Archived Q4 only; ER/history provisional. One original posting date is not a trend. Fixed OEWS card stays national 15-1252 / 2025.'),(94000,DISCLAIMER)]):
        zone=sub(zones,'zone',id=20+i,x=0,y=y,w=100000,h=6000,**{'type-v2':'text'})
        sub(sub(zone,'formatted-text'),'run',text)
    sub(dashboard,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/dashboard')).upper()+'}')
    comparison_dashboard=sub(dashboards,'dashboard',name=COMPARISON_DASHBOARD)
    sub(comparison_dashboard,'style');sub(comparison_dashboard,'size',maxheight=1000,maxwidth=1440,minheight=1000,minwidth=1440)
    zones=sub(comparison_dashboard,'zones')
    for i,name in enumerate(PARAMETERS):
        sub(zones,'zone',id=30+i,x=i*25000,y=8000,w=25000,h=12000,**{'type-v2':'paramctrl','name':'Parameters','param':'[Parameters].['+name+']','show-title':'true'})
    for i,name in enumerate(COMPARISON_SHEETS):
        x,y,w,h=(i*33333,23000,33333,20000) if i<3 else (0,46000+(i-3)*15000,100000,13000)
        sub(zones,'zone',id=40+i,name=name,x=x,y=y,w=w,h=h)
    for i,(y,text) in enumerate([(0,'Matched wages | Edit shared SOC and area once for all four values. Annual USD; percentiles are not OEWS ranks.'),(92000,DISCLAIMER)]):
        zone=sub(zones,'zone',id=50+i,x=0,y=y,w=100000,h=7000,**{'type-v2':'text'});sub(sub(zone,'formatted-text'),'run',text)
    sub(comparison_dashboard,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/comparison-dashboard')).upper()+'}')
    windows=sub(root,'windows')
    for name in sheets:
        window=sub(windows,'window',name=name,**{'class':'worksheet'})
        strip=sub(sub(sub(window,'cards'),'edge',name='right'),'strip',size=200)
        for field in sheet_controls[name]:sub(strip,'card',type='filter',param=field,mode='checkdropdown')
        sub(window,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/window/'+name)).upper()+'}')
    window=sub(windows,'window',name=dashboard_name,**{'class':'dashboard','maximized':'true'})
    sub(window,'viewpoints');sub(window,'active',id=1)
    sub(window,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/dashboard-window')).upper()+'}')
    window=sub(windows,'window',name=COMPARISON_DASHBOARD,**{'class':'dashboard'})
    sub(window,'viewpoints');sub(window,'active',id=40)
    sub(window,'simple-id',uuid='{'+str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/comparison-window')).upper()+'}')
    ET.indent(root)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only',action='store_true',help='Repackage edited XML while preserving verified Hyper bytes')
    args=parser.parse_args()
    os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    con, manifest, manifest_sha=connect_snapshot(verify_contents=not args.source_only)
    schemas={name:[(r[0],r[1]) for r in con.execute(f'DESCRIBE {name}').fetchall()] for name in CALCS}
    OUT.mkdir(exist_ok=True)
    if args.source_only:
        twb=workbook(schemas)
        temporary=BI/'.work/repacked.twbx'
        with zipfile.ZipFile(OUT/'sponsoratlas.twbx') as old,zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as new:
            for name in old.namelist():new.writestr(name,twb if name=='sponsoratlas.twb' else old.read(name))
        temporary.replace(OUT/'sponsoratlas.twbx');(OUT/'sponsoratlas.twb').write_bytes(twb)
        for evidence in ['hyper-extract.json','extract-verification.json']:
            path=BI/'evidence'/evidence;record=json.loads(path.read_text());record['twbx_sha256']=sha(OUT/'sponsoratlas.twbx');record['xml_repack_preserved_extracts']=True;path.write_text(json.dumps(record,indent=2)+'\n')
        con.close();print('TWB actions serialized; existing verified Hyper bytes preserved');return
    record={'origin':'ACTUAL_HYPER_EXTRACT_API_AND_SQL_NOT_TABLEAU_VIZQL',
            'manifest_sha256':manifest_sha,'executed_at':datetime.datetime.now(datetime.UTC).isoformat(),
            'sdk_import':str(Path(tableauhyperapi.__file__).relative_to(ROOT)),
            'cffi_import':str(Path(cffi.__file__).relative_to(ROOT)),
            'telemetry':'DO_NOT_SEND_USAGE_DATA_TO_TABLEAU','memory_limit':'1G','cpu_affinity':sorted(os.sched_getaffinity(0)), 'extracts':{}}
    params={'memory_limit':'1G','log_dir':str(BI/'.work'),'log_file_max_count':'2','log_file_size_limit':'1M','default_database_version':'3'}
    with tempfile.TemporaryDirectory(prefix='extract-',dir=BI/'.work') as tmp:
        tmp=Path(tmp)
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,parameters=params) as hyper:
            for fact,schema in schemas.items():
                file=tmp/(fact+'.hyper')
                table=TableDefinition(TableName('Extract','Extract'),[TableDefinition.Column(name,TYPE[typ][1]()) for name,typ in schema])
                with Connection(hyper.endpoint,str(file),CreateMode.CREATE) as native:
                    native.catalog.create_schema('Extract');native.catalog.create_table(table)
                    cursor=con.execute(f'SELECT * FROM {fact}');rows=0
                    while batch:=cursor.fetchmany(10000):
                        rows+=len(batch)
                        if rows>1_000_000:raise ValueError('Extract row cap')
                        with Inserter(native,table) as insert:
                            insert.add_rows(batch);insert.execute()
                    expected=next(e['rows'] for e in manifest['exports'] if Path(e['path']).stem==fact)
                    count=native.execute_scalar_query('SELECT COUNT(*) FROM "Extract"."Extract"')
                    if count!=expected or rows!=expected:raise ValueError('Extract accounting')
                    if fact=='lca':
                        sql='SELECT COUNT(*), SUM(CASE WHEN is_certified THEN 1 ELSE 0 END)::DOUBLE PRECISION/COUNT(*), PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY annual_wage) FROM "Extract"."Extract"'
                    elif fact=='postings':
                        sql='SELECT SUM(CASE WHEN is_active THEN 1 ELSE 0 END) FROM "Extract"."Extract"'
                    else:
                        sql='SELECT CASE WHEN COUNT(*)=1 THEN MAX(annual_median_wage) END FROM "Extract"."Extract" WHERE soc=\'15-1252\' AND area=\'1:99\' AND survey_year=2025'
                    values=[[float(v) if isinstance(v,decimal.Decimal) else v for v in row] for row in native.execute_list_query(sql)]
                    record['extracts'][fact]={'rows':rows,'sql':sql,'values':values}
                    record['engine']=native.execute_scalar_query('SELECT version()')
                record['extracts'][fact].update(bytes=file.stat().st_size,sha256=sha(file))
                if sum(p.stat().st_size for p in tmp.iterdir())>150_000_000:raise ValueError('Extract disk cap')
        twb=workbook(schemas)
        (OUT/'sponsoratlas.twb').write_bytes(twb)
        with zipfile.ZipFile(OUT/'sponsoratlas.twbx','w',compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr('sponsoratlas.twb',twb)
            for fact in CALCS:package.write(tmp/(fact+'.hyper'),f'Data/{fact}.hyper')
            package.writestr('source-manifest.json',json.dumps({'original_manifest_sha256':manifest_sha,'exports':manifest['exports']},indent=2))
    con.close()
    record['hyper_children_max_rss_kib']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    record['python_max_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record['twbx_sha256']=sha(OUT/'sponsoratlas.twbx')
    record['native_tableau_open_render']='NOT_EXECUTED'
    (BI/'evidence/hyper-extract.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Three native Hyper extracts created/queried; TWBX packaged; Tableau GUI/VizQL still OPEN')


if __name__=='__main__':main()
