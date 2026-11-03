"""Read only the reviewed public snapshot, verifying its manifest and grain."""
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / '.tools/semantic/actual'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def connect_snapshot(directory=SNAPSHOT, *, verify_contents=True):
    """Default full verification; schema-only generators may reuse prior verified bytes.

    verify_contents=False checks manifest kind, file size and readonly mode,
    without rescanning hashes or grains; it does not refresh data evidence.
    """
    directory = Path(directory).resolve()
    manifest_path = directory / 'export-evidence.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['kind'] != 'ACTUAL_PUBLIC_MARTS':
        raise ValueError('Actual local public marts required; fixtures are separate tests')
    con = duckdb.connect(config={'threads': 1, 'memory_limit': '512MB'})
    keys = {'lca': 'case_number', 'postings': '(posting_id, observation_date)',
            'wages': '(soc, area, survey_year)'}
    for name, key in keys.items():
        path = directory / (name + '.parquet')
        record = next(e for e in manifest['exports'] if Path(e['path']) == path)
        if ((verify_contents and sha(path) != record['sha256']) or path.stat().st_size != record['size']
                or path.stat().st_mode & 0o222):
            raise ValueError(f'Manifest/protection mismatch: {name}')
        con.read_parquet(str(path)).create_view(name)
        if not verify_contents:
            continue
        count, unique = con.execute(f'SELECT count(*),count(DISTINCT {key}) FROM {name}').fetchall()[0]
        if count != record['rows'] or count != unique:
            raise ValueError(f'Grain mismatch: {name}')
    return con, manifest, sha(manifest_path)
