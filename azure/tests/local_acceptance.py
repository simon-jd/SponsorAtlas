"""Operational RED/GREEN on synthetic DuckDB, with declared T-SQL adaptation."""
import json
import sys
import duckdb
from azure.tests.test_sql import SQLBehavior,adapt,SQL
from azure.tests.test_port import envelope,ROOT
from azure.fabric.contract import annual_map,normalize,latest,metrics,compare

if sys.argv[1]=='red':
    with duckdb.connect(':memory:',config={'memory_limit':'512MB','threads':1,'max_temp_directory_size':'64MB'}) as db:
        db.execute('CREATE SCHEMA MARTS')
        print('LOCAL_SYNTHETIC_DUCKDB_ADAPTED_SQL: validation against absent target',flush=True)
        db.execute(adapt((SQL/'verify-fabric.sql').read_text()))
elif sys.argv[1]=='green':
    fixture=SQLBehavior();fixture.setUp()
    try:
        mapping=annual_map(ROOT/'dbt/seeds/lca_column_map.csv',2025)
        rows=[normalize(envelope(case=c,wage=w,decision=d),2025,mapping) for c,w,d in [('A','10','2024-01-01'),('A','20','2025-01-01'),('B','30','2025-01-01'),('C','','2025-01-01')]]
        for i,row in enumerate(rows):row['source_row_number']=i+2
        rows[2]['case_status']='Denied';rows[3]['case_status']='Certified - Withdrawn'
        fixture.insert(rows)
        query=adapt((SQL/'fact-select.sql').read_text())
        fixture.db.execute('INSERT INTO MARTS.fact_lca_case '+query)
        result=dict(zip(('case_count','certified_rate','wage_p50','wage_p90'),fixture.db.execute(adapt((SQL/'verify-fabric.sql').read_text())).fetchone()))
        expected=metrics(latest(rows))
        identity=dict(scope='SYNTHETIC',manifest_sha256='a'*64,model_version='fixture',registry_sha256='b'*64,history_sha256='c'*64)
        compare(dict(identity=identity,metrics=expected),dict(identity=identity,metrics=result))
        fixture.db.execute('DELETE FROM MARTS.fact_lca_case')
        fixture.db.execute('INSERT INTO MARTS.fact_lca_case '+query)
        again=dict(zip(result,fixture.db.execute(adapt((SQL/'verify-fabric.sql').read_text())).fetchone()))
        if result!=again:raise AssertionError('idempotence failed')
        print(json.dumps({'kind':'LOCAL_SYNTHETIC_DUCKDB_ADAPTED_SQL','status':'PASS','input_rows':len(rows),'actual':result,'independent_python_oracle':expected,'idempotence':True},default=str))
    finally:fixture.tearDown()
else:raise ValueError('red or green required')
