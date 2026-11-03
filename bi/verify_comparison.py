"""Bounded local SQL scope oracle; never DAX/VizQL evidence or native gate input."""
import datetime
import json
import re
from snapshot import ROOT, connect_snapshot


def reference(con, soc, area, fiscal_year, survey_year):
    supported=area=='1:99' or re.fullmatch(r'4:[0-9]{5}',area) is not None
    result={'soc':soc,'published_area':area,'lca_fiscal_year':fiscal_year,'oews_survey_year':survey_year,'supported':supported}
    if not supported:
        return result | {'lca_rows':0,'non_null_wages':0,'p50':None,'p75':None,'p90':None,'estimate_count':0,'oews_median':None,'ratio_p75':None,'status':'UNSUPPORTED_AREA'}
    rows,nonnull,p50,p75,p90=con.execute('''SELECT count(*),count(annual_wage),
        quantile_cont(annual_wage,.5),quantile_cont(annual_wage,.75),quantile_cont(annual_wage,.9)
        FROM lca WHERE soc=? AND (?='1:99' OR metro=substr(?,3)) AND (?=0 OR fiscal_year=?)''',[soc,area,area,fiscal_year,fiscal_year]).fetchone()
    estimates,median=con.execute('''SELECT count(*),CASE WHEN count(*)=1 THEN max(annual_median_wage) END
        FROM wages WHERE soc=? AND area=? AND survey_year=?''',[soc,area,survey_year]).fetchone()
    p50,p75,p90,median=[None if value is None else float(value) for value in (p50,p75,p90,median)]
    status='NO_LCA_WAGES' if not nonnull else 'NO_ESTIMATE' if not estimates else 'MULTIPLE_ESTIMATES' if estimates!=1 else 'SUPPRESSED_OR_TOP_CODED' if median is None else 'MATCHED'
    return result | {'lca_rows':rows,'non_null_wages':nonnull,'p50':p50,'p75':p75,'p90':p90,'estimate_count':estimates,'oews_median':median,'ratio_p75':p75/median if p75 is not None and median else None,'status':status}


def main():
    con,_,manifest_sha=connect_snapshot(verify_contents=False)
    requests=[('15-1252','1:99',0,2025),('15-1252','4:35620',0,2025),('15-1252','4:35620',2025,2025),('15-1211','4:35620',0,2025),('15-1252','2:06',0,2025),('15-1252','4:00000',0,2025)]
    results=[reference(con,*request) for request in requests]
    national,metro,fy,other_soc,unsupported,unmatched=results
    assert national['oews_median']==135980 and national['lca_rows']>metro['lca_rows']
    assert metro['lca_rows']==3319 and metro['oews_median']==166830 and metro['status']=='MATCHED'
    assert metro['p75']!=national['p75'] and metro['ratio_p75']==metro['p75']/166830
    assert fy['lca_rows']==420 and fy['oews_median']==metro['oews_median']
    assert other_soc['status']=='MATCHED' and other_soc['p75']!=metro['p75']
    assert unsupported['status']=='UNSUPPORTED_AREA' and unsupported['p75'] is None and unsupported['oews_median'] is None
    assert unmatched['p75'] is None and unmatched['oews_median'] is None
    con.close()
    record={'origin':'LOCAL_SQL_SCOPE_REGRESSION_NOT_DAX_OR_VIZQL','executed_at':datetime.datetime.now(datetime.UTC).isoformat(),'manifest_sha256':manifest_sha,'source_check':'existing verified public snapshot; current readonly/size checks only, no repeated full hashes','results':results,'status':'PASS','native_acceptance':'OPEN'}
    (ROOT/'bi/evidence/comparison-scope.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
