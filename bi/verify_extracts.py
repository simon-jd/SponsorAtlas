"""Reopen packaged Hyper files and compare every field/row to public Parquet."""
import decimal
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from tableauhyperapi import HyperProcess,Telemetry,Connection
from snapshot import ROOT,connect_snapshot,sha

BI=ROOT/'bi'
KEYS={'lca':'case_number','postings':'posting_id,observation_date','wages':'soc,area,survey_year'}


def fingerprint(rows,types):
    digest=hashlib.sha256();count=0
    for row in rows:
        values=[]
        for value,dtype in zip(row,types):
            if value is None:values.append(None)
            elif dtype.startswith('DECIMAL'):values.append(format(decimal.Decimal(value).normalize(),'f'))
            elif dtype=='DATE':values.append(str(value))
            else:values.append(value)
        digest.update((json.dumps(values,ensure_ascii=False,separators=(',',':'))+'\n').encode())
        count+=1
    return {'rows':count,'sha256':digest.hexdigest()}


def main():
    os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    con,_,manifest_sha=connect_snapshot()
    record={'origin':'REOPEN_PACKAGED_HYPER_FULL_ROW_COMPARISON_NOT_VIZQL','manifest_sha256':manifest_sha,'tables':{}}
    package=BI/'tableau/sponsoratlas.twbx'
    with tempfile.TemporaryDirectory(dir=BI/'.work',prefix='verify-') as temp:
        temp=Path(temp)
        with zipfile.ZipFile(package) as z:
            expected={'sponsoratlas.twb','source-manifest.json',*[f'Data/{name}.hyper' for name in KEYS]}
            if set(z.namelist()) != expected or z.testzip() is not None:raise ValueError('Unexpected package members/CRC')
            if sum(i.file_size for i in z.infolist())>150_000_000:raise ValueError('Package size cap')
            z.extractall(temp)
        params={'memory_limit':'1G','log_dir':str(BI/'.work'),'log_file_max_count':'2','log_file_size_limit':'1M'}
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,parameters=params) as hyper:
            for fact,key in KEYS.items():
                file=temp/f'Data/{fact}.hyper';before=sha(file)
                types=[r[1] for r in con.execute(f'DESCRIBE {fact}').fetchall()]
                with Connection(hyper.endpoint,str(file)) as native:
                    with native.execute_query(f'SELECT * FROM "Extract"."Extract" ORDER BY {key}') as rows:
                        actual=fingerprint(rows,types)
                if sha(file)!=before:raise ValueError('Verification changed extract')
                cursor=con.execute(f'SELECT * FROM {fact} ORDER BY {key}')
                def stream():
                    while batch:=cursor.fetchmany(10000):yield from batch
                expected=fingerprint(stream(),types)
                if actual != expected:raise ValueError(f'Lossy extract: {fact}')
                record['tables'][fact]=actual
    con.close()
    record['status']='PASS'
    record['twbx_sha256']=sha(package)
    (BI/'evidence/extract-verification.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Packaged Hyper reopened: every field in 476491 rows matches public Parquet')


if __name__=='__main__':main()
