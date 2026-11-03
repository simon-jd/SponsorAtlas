# Fabric notebook source. Import as a Python notebook; attach the intended Lakehouse.
# Parameters (set explicitly by authorized operator after ADF Copy succeeds):
BUNDLE_ROOT = None  # /lakehouse/default/Files/sponsoratlas/<manifest SHA>/
EXPECTED_MANIFEST_SHA256 = None


def materialize(spark,bundle_root,expected_manifest_sha256):
    """Native Fabric Spark only. No Spark is started/imported by local validation."""
    import json
    import hashlib
    from pathlib import Path
    from datetime import date
    from pyspark.sql import types as T, functions as F

    root=Path(bundle_root)
    if not str(root).startswith('/lakehouse/default/Files/sponsoratlas/'):
        raise ValueError('attached Lakehouse bundle path required')
    manifest_path=root/'manifest.json'
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest()!=expected_manifest_sha256: raise ValueError('manifest digest mismatch')
    manifest=json.loads(manifest_path.read_text())
    code_path=root/'azure-port-code.zip'
    if hashlib.sha256(code_path.read_bytes()).hexdigest()!=manifest['code_sha256']:raise ValueError('portable code digest differs')
    spark.sparkContext.addPyFile(str(code_path))
    from sponsoratlas_port.archive import verify_bundle,contained
    from sponsoratlas_port.contract import annual_map,normalize
    # Preserve full original payload in Files. Scan streaming, not driver read_bytes.
    verify_bundle(root,manifest)
    # A READY Lakehouse is immutable. Repeated same-batch calls are no-ops;
    # another batch requires a different dedicated Lakehouse, never live overwrite.
    if spark.catalog.tableExists('port_identity'):
        ready=spark.table('port_identity').collect()
        if len(ready)!=1 or ready[0]['manifest_sha256']!=expected_manifest_sha256:
            raise ValueError('Lakehouse already sealed for another batch')
        return {'kind':'NATIVE_FABRIC_LAKEHOUSE_READY','idempotent_noop':True,'manifest_sha256':expected_manifest_sha256}
    mapping_path=contained(root,manifest['mapping_path'])
    frames=[]
    for archive in manifest['archives']:
        year=int(archive['release'][2:6]); mapping=annual_map(mapping_path,year)
        names=list(mapping)+['source_release','source_sha256','source_url','source_row_number','source_system','fiscal_year',
            'received_date','decision_date','begin_date','end_date','employer_state','worksite_state','soc_code',
            'worksite_county','wage_amount','annual_wage','worker_positions','source_employer_key']
        def convert(line,year=year,mapping=mapping,names=names):
            row=normalize(json.loads(line.value),year,mapping)
            for k in ('received_date','decision_date','begin_date','end_date'):
                if row[k] is not None: row[k]=date.fromisoformat(row[k])
            return tuple(row[k] for k in names)
        types={k:T.StringType() for k in names}
        types.update(source_row_number=T.LongType(),fiscal_year=T.IntegerType(),wage_amount=T.DecimalType(18,2),annual_wage=T.DecimalType(24,2),worker_positions=T.DecimalType(38,0))
        for k in ('received_date','decision_date','begin_date','end_date'):types[k]=T.DateType()
        schema=T.StructType([T.StructField(k,types[k],True) for k in names])
        # Read in executor partitions; schema explicit even for all-null columns.
        relative='Files/'+str(contained(root,archive['path'])).split('/Files/',1)[1]
        frames.append(spark.createDataFrame(spark.read.text(relative).rdd.map(convert),schema))
    frame=frames[0].unionByName(frames[1])
    key=['source_system','source_release','source_sha256','source_row_number']
    if frame.groupBy(key).count().filter('count<>1').limit(1).count(): raise ValueError('duplicate source occurrence')
    if frame.count()!=sum(a['rows'] for a in manifest['archives']): raise ValueError('Delta source count differs')
    frame.write.format('delta').mode('overwrite').option('overwriteSchema','true').saveAsTable('port_lca')
    for ref in manifest['references']:
        relative='Files/'+str(contained(root,ref['path'])).split('/Files/',1)[1]
        # Avoid inferred numbers and preserve IDs/leading zeroes exactly.
        first=contained(root,ref['path']).open()
        try:
            import csv
            columns=list(next(csv.DictReader(first))) if ref['format']=='csv' else list(json.loads(next(first)))
        finally:first.close()
        schema=T.StructType([T.StructField(k,T.StringType(),True) for k in columns])
        reader=spark.read.schema(schema)
        data=reader.option('header','true').csv(relative) if ref['format']=='csv' else reader.json(relative)
        if data.count()!=ref['rows']:raise ValueError('reference rows differ after Spark parsing')
        if data.filter(F.greatest(*[F.length(F.encode(F.col(c),'UTF-8')) for c in columns])>8000).limit(1).count():raise ValueError('reference UTF-8 truncation')
        data.write.format('delta').mode('overwrite').option('overwriteSchema','true').saveAsTable('port_'+ref['table'])
    identity={k:manifest[k] for k in ('scope','model_version','registry_sha256','history_sha256')}
    identity['manifest_sha256']=expected_manifest_sha256
    order=('scope','manifest_sha256','model_version','registry_sha256','history_sha256')
    spark.createDataFrame([tuple(identity[k] for k in order)],list(order)).write.format('delta').mode('errorifexists').saveAsTable('port_identity')
    return {'kind':'NATIVE_FABRIC_LAKEHOUSE_READY','identity':identity,'archive_rows':sum(a['rows'] for a in manifest['archives'])}


if __name__=='__main__':
    if not BUNDLE_ROOT or not EXPECTED_MANIFEST_SHA256: raise ValueError('explicit immutable bundle identity required')
    print(materialize(spark,BUNDLE_ROOT,EXPECTED_MANIFEST_SHA256))  # noqa: F821 native notebook session
