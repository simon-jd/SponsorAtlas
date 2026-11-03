"""Reconcile recorded real engines; absent native evidence is always OPEN (exit 1)."""
import argparse
import csv
import json
import math
from pathlib import Path

from snapshot import ROOT, connect_snapshot, sha

BI = ROOT / 'bi'


def load_native(con, directory, manifest_sha):
    """Human/native export attestations are evidence, not cryptographic engine proof.

    Require original exports, screenshots, artifact hashes and all query identities.
    A reviewer still verifies the application's actual open/render/execution record.
    """
    for engine, artifact in [('powerbi_dax', 'powerbi/sponsoratlas.pbix'),
                              ('tableau', 'tableau/sponsoratlas.twbx')]:
        metadata = directory / f'{engine}.json'
        if not metadata.exists():
            continue
        evidence = json.loads(metadata.read_text())
        required = ['application_version', 'executed_at', 'export_file', 'screenshot_file']
        if any(not evidence.get(key) for key in required):
            raise ValueError(f'{engine}: incomplete execution provenance')
        if (evidence.get('engine') != engine or evidence.get('manifest_sha256') != manifest_sha
                or evidence.get('queries_sha256') != sha(BI / 'queries.json')
                or evidence.get('artifact_sha256') != sha(BI / artifact)):
            raise ValueError(f'{engine}: provenance mismatch')
        for key in ['export_file', 'screenshot_file']:
            file = (directory / evidence[key]).resolve()
            if not file.is_relative_to(directory.resolve()) or not file.is_file():
                raise ValueError(f'{engine}: missing/unsafe {key}')
            if sha(file) != evidence.get(key + '_sha256'):
                raise ValueError(f'{engine}: changed {key}')
        with (directory / evidence['export_file']).open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        expected = set(json.loads((BI / 'queries.json').read_text()))
        if len(rows) != 5 or {row['metric'] for row in rows} != expected:
            raise ValueError(f'{engine}: five unique metric rows required')
        for row in rows:
            value = None if row['value'] == '' else float(row['value'])
            if value is not None and not math.isfinite(value):
                raise ValueError(f'{engine}: invalid numeric value')
            con.execute('INSERT INTO native_results VALUES (?,?,?)', [engine,row['metric'],value])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oracle-only', action='store_true', help='Check Cube vs SQL only; never native acceptance')
    parser.add_argument('--native-dir', type=Path, default=BI / 'native-results')
    args = parser.parse_args()
    con, manifest, manifest_sha = connect_snapshot()
    con.execute('CREATE TABLE cube_results(metric VARCHAR PRIMARY KEY,value DOUBLE)')
    con.execute('CREATE TABLE native_results(engine VARCHAR,metric VARCHAR,value DOUBLE,PRIMARY KEY(engine,metric))')
    evidence = json.loads((BI / 'evidence/cube-rest.json').read_text())
    queries = json.loads((BI / 'queries.json').read_text())
    if (evidence['origin'] != 'ACTUAL_CUBE_1.8_REST' or evidence['manifest_sha256'] != manifest_sha
            or evidence['queries_sha256'] != sha(BI / 'queries.json')):
        raise ValueError('Cube provenance mismatch')
    if len(evidence['records']) != 5:
        raise ValueError('Expected five Cube executions')
    for record in evidence['records']:
        metric = record['metric']
        if record['query'] != queries[metric] or record['status'] != 200:
            raise ValueError('Cube query/status mismatch')
        value = record['body']['data'][0][queries[metric]['measures'][0]]
        con.execute('INSERT INTO cube_results VALUES (?,?)', [metric, None if value is None else float(value)])
    load_native(con, args.native_dir, manifest_sha)
    cursor = con.execute((BI / 'reconcile.sql').read_text())
    columns = [c[0] for c in cursor.description]
    rows = [dict(zip(columns,row)) for row in cursor.fetchall()]
    sql_match = all((r['sql_oracle'] is None and r['cube_rest'] is None) or
                    (r['sql_oracle'] is not None and r['cube_rest'] is not None
                     and abs(r['sql_oracle']-r['cube_rest']) <= 1e-8) for r in rows)
    result = {'snapshot_kind': manifest['kind'], 'manifest_sha256': manifest_sha,
              'sql_engine': 'DuckDB ' + con.execute('SELECT version()').fetchall()[0][0],
              'cube_sql_match':sql_match, 'native_acceptance':all(r['status']=='PASS' for r in rows),
              'rows':rows}
    (BI / 'evidence/reconciliation.json').write_text(json.dumps(result,indent=2)+'\n')
    for row in rows:
        print(row)
    print('Cube/SQL:', 'PASS' if sql_match else 'FAIL', '| Native acceptance:', 'PASS' if result['native_acceptance'] else 'OPEN')
    con.close()
    return 0 if sql_match and (args.oracle_only or result['native_acceptance']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
