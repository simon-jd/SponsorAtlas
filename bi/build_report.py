"""Power BI PBIR source layout, awaiting actual Desktop open/refresh/render."""
import json
import uuid
from snapshot import ROOT

BASE = ROOT/'bi/powerbi'
REPORT = BASE/'SponsorAtlas.Report'
SCHEMA = 'https://developer.microsoft.com/json-schemas/fabric/item/report/'


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def projection(table,field,measure=True):
    return {'field':{'Measure' if measure else 'Column':{'Expression':{'SourceRef':{'Entity':table}},'Property':field}},'queryRef':table+'.'+field}


def main():
    write(BASE/'sponsoratlas.pbip',{'$schema':'https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json','version':'1.0','artifacts':[{'report':{'path':'SponsorAtlas.Report'}}],'settings':{'enableAutoRecovery':True}})
    for folder,kind in [(REPORT,'Report'),(BASE/'SponsorAtlas.SemanticModel','SemanticModel')]:
        write(folder/'.platform',{'$schema':'https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json','metadata':{'type':kind,'displayName':'SponsorAtlas'},'config':{'version':'2.0','logicalId':str(uuid.uuid5(uuid.NAMESPACE_URL,'SponsorAtlas/bi/'+kind))}})
    write(REPORT/'definition.pbir',{'$schema':SCHEMA+'definitionProperties/2.0.0/schema.json','version':'4.0','datasetReference':{'byPath':{'path':'../SponsorAtlas.SemanticModel'}}})
    definition=REPORT/'definition'
    write(definition/'version.json',{'$schema':SCHEMA+'definition/versionMetadata/1.0.0/schema.json','version':'2.0.0'})
    write(definition/'report.json',{'$schema':SCHEMA+'definition/report/3.1.0/schema.json','themeCollection':{}})
    pages=['Overview','WageComparison','DataQuality']
    write(definition/'pages/pages.json',{'$schema':SCHEMA+'definition/pagesMetadata/1.0.0/schema.json','pageOrder':pages,'activePageName':'Overview'})
    for page in pages:
        write(definition/f'pages/{page}/page.json',{'$schema':SCHEMA+'definition/page/2.0.0/schema.json','name':page,'displayName':page,'displayOption':'FitToPage','width':1440,'height':1000})
    serial=0
    def visual(page,kind,title,x,y,w,h,roles):
        nonlocal serial
        serial+=1
        name=f'visual{serial:03}'
        v={'visualType':kind,'query':{'queryState':{role:{'projections':items} for role,items in roles.items()}},
           'visualContainerObjects':{'title':[{'properties':{'text':{'expr':{'Literal':{'Value':"'"+title.replace("'","''")+"'"}}},'show':{'expr':{'Literal':{'Value':'true'}}}}}]}}
        write(definition/f'pages/{page}/visuals/{name}/visual.json',{'$schema':SCHEMA+'definition/visualContainer/2.1.0/schema.json','name':name,'position':{'x':x,'y':y,'width':w,'height':h,'z':serial,'tabOrder':serial},'visual':v})
    def card(page,table,field,title,x,y,w=270,h=120):
        visual(page,'card',title,x,y,w,h,{'Values':[projection(table,field)]})
    card('Overview','lca','Coverage Status','SponsorAtlas | public-source coverage',20,0,1400,80)
    cards=[('lca','Case Count','Latest LCA cases'),('lca','Certified Rate','Certified + Certified - Withdrawn / all cases'),('lca','Wage P75','Annual wage p75 (USD)'),('postings','Active Posting Days','Active observed posting-days'),('wages','Reconcile OEWS Median','Fixed benchmark: national 15-1252, survey 2025')]
    for i,(table,field,title) in enumerate(cards):card('Overview',table,field,title,20+i*280,90)
    for i,(table,field) in enumerate([('Employer','employer'),('SOC','soc'),('Metro','metro'),('FiscalYear','fiscal_year')]):
        visual('Overview','slicer',field,20+i*350,220,340,90,{'Values':[projection(table,field,False)]})
    for i,(table,field,title) in enumerate([('Employer','employer','Cases by employer (provisional identity)'),('SOC','soc','Cases by detailed SOC'),('Metro','metro','Cases by metropolitan CBSA')]):
        visual('Overview','barChart',title,20+i*470,320,460,320,{'Category':[projection(table,field,False)],'Y':[projection('lca','Case Count')]})
    visual('Overview','columnChart','Original UTC observation dates (one date: no trend)',20,660,920,220,{'Category':[projection('ObservationDate','observation_date',False)],'Y':[projection('postings','Active Posting Days')]})
    card('Overview','postings','Trend Status','Observation coverage',960,660,460,220)
    for i,(table,field) in enumerate([('SOC','soc'),('PublishedArea','area'),('SurveyYear','survey_year'),('FiscalYear','fiscal_year')]):
        visual('WageComparison','slicer',field,20+i*350,90,340,90,{'Values':[projection(table,field,False)]})
    for i,p in enumerate((50,75,90)):card('WageComparison','lca',f'Comparison Wage P{p}',f'Matched LCA annual wage p{p} USD',20+i*350,220,340,160)
    card('WageComparison','wages','Comparison OEWS Median','Matched official OEWS A_MEDIAN (USD)',20,410,440,170)
    card('WageComparison','lca','Wage P75 vs OEWS','LCA p75 / official annual median',480,410,440,170)
    card('WageComparison','lca','Comparison Scope','Shared SOC and published area | National 1:99 / Metropolitan 4:CBSA',20,610,1400,120)
    visual('DataQuality','tableEx','Unknown/provisional history remains visible',20,90,1400,300,{'Values':[projection('lca','employer_history_status',False),projection('lca','Case Count')]})
    visual('DataQuality','tableEx','Published wage status and suppressed values',20,410,1400,300,{'Values':[projection('wages','wage_status',False),projection('wages','median_raw',False),projection('wages','OEWS Estimate Count')]})
    for page in pages:card(page,'lca','Disclaimer','How to interpret these data',20,900,1400,90)
    print(f'PBIP/PBIR source: {len(pages)} pages, {serial} visuals; native open/render pending')


if __name__=='__main__':main()
