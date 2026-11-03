"""Portable archive normalization and independent small-fixture parity oracle.

Normalization is also used by the Fabric notebook. latest/history/metrics are a
synthetic oracle; the production Warehouse implementation is T-SQL.
"""
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
import re


def annual_map(path, year):
    with open(path, newline='') as stream:
        rows = [r for r in csv.DictReader(stream) if int(r['fiscal_year']) == year and r['disposition'] == 'mapped']
    result = {r['standard_column']: r['source_column'] for r in rows}
    if not result or len(result) != len(rows):
        raise ValueError('annual schema mapping missing or duplicated')
    return result


def number(value, precision=18, scale=2):
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            val = Decimal(value).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
            if not val.is_finite() or abs(val) >= Decimal(10) ** (precision-scale):
                return None
            return val
    except (InvalidOperation, TypeError, ValueError):
        return None


def date_value(value):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime((value or '').strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def normalize(envelope, year, mapping):
    payload = json.loads(envelope['payload_json'])
    if not isinstance(payload, dict) or not set(mapping.values()) <= payload.keys():
        raise ValueError('annual payload schema differs from mapped source headers')
    if any(not isinstance(v, str) for v in payload.values()):
        raise ValueError('archive values must preserve strings')
    if envelope['source_release'] != f'FY{year}_Q4' or not re.fullmatch('[0-9a-f]{64}', envelope['source_sha256']):
        raise ValueError('release/source identity mismatch')
    if type(envelope['source_row_number']) is not int or envelope['source_row_number'] < 1:
        raise ValueError('invalid source row')
    row = {name: payload[col] or None for name,col in mapping.items()}
    row.update({k:envelope[k] for k in ('source_release','source_sha256','source_url','source_row_number')})
    row.update(source_system='lca', fiscal_year=year)
    for col in ('received_date','decision_date','begin_date','end_date'):
        row[col] = date_value(row[col+'_raw'])
    for col in ('employer_state','worksite_state'):
        row[col] = row[col+'_raw'].strip().upper() if row[col+'_raw'] is not None else None
    raw = row['soc_code_raw'] or ''
    raw = raw.strip()
    row['soc_code'] = raw[:7] if re.fullmatch(r'[0-9]{2}-[0-9]{4}(\.[0-9]{2})?', raw) else (raw[:2]+'-'+raw[2:] if re.fullmatch(r'[0-9]{6}', raw) else None)
    county = row['worksite_county_raw']
    row['worksite_county'] = re.sub(r'\s+', ' ', county.replace('.', '').replace("'", '')).strip().upper() if county is not None else None
    row['wage_amount'] = number(row['wage_amount_raw'])
    factor = {'Hour':2080,'Week':52,'Bi-Weekly':26,'Month':12,'Year':1}.get(row['wage_unit'])
    row['annual_wage'] = row['wage_amount']*factor if row['wage_amount'] is not None and factor else None
    positions = (row['worker_positions_raw'] or '').strip()
    row['worker_positions'] = number(positions,38,0) if re.fullmatch(r'[+-]?[0-9]+(\.0+)?',positions) else None
    source_key = '|'.join(row[x] or '' for x in ('employer_country','employer_name','employer_state','employer_fein','employer_city','employer_zip'))
    row['source_employer_key'] = hashlib.md5(source_key.encode()).hexdigest()
    # Lakehouse SQL strings map to varchar(8000); fail rather than truncate.
    if any(isinstance(v,str) and len(v.encode('utf-8')) > 8000 for v in row.values()):
        raise ValueError('UTF-8 value exceeds Warehouse staging string width')
    return row


def latest(rows):
    occurrences, groups = set(), {}
    for row in rows:
        if not row.get('case_number') or not row['case_number'].strip():
            raise ValueError('missing case grain')
        key = tuple(row[x] for x in ('source_system','source_release','source_sha256','source_row_number'))
        if key in occurrences:
            raise ValueError('duplicate source occurrence')
        occurrences.add(key)
        groups.setdefault(row['case_number'], []).append(row)
    result = []
    for case, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda r:(r['source_sha256'],r['source_row_number']))
        for name in ('source_release','fiscal_year','received_date','decision_date'):
            ordered.sort(key=lambda r:(r[name] is not None,r[name] or ''), reverse=True)
        winner = dict(ordered[0])
        tie = sum(all(r[k] == winner[k] for k in ('decision_date','received_date','fiscal_year')) for r in group)
        winner.update(source_occurrence_count=len(group), latest_selection_status='SAME_DATE_TIE_REVIEW_REQUIRED' if tie>1 else 'UNAMBIGUOUS_LATEST')
        result.append(winner)
    return result


def attach_history(row, history):
    versions = sorted((r for r in history if r['employer_id']==row.get('employer_id')), key=lambda r:r['valid_from'])
    for i, version in enumerate(versions):
        if version['valid_to'] is not None and version['valid_to'] <= version['valid_from']:
            raise ValueError('invalid history interval')
        if i and (versions[i-1]['valid_to'] is None or versions[i-1]['valid_to'] > version['valid_from']):
            raise ValueError('overlapping history intervals')
    matches = []
    status = 'DATE_PRECISION_OR_INTERVAL_UNKNOWN'
    if row.get('employer_id') is None:
        status = 'UNRESOLVED_SOURCE_EMPLOYER'
    elif row['decision_date'] is None:
        status = 'SOURCE_FACT_DATE_MISSING'
    else:
        start = datetime.fromisoformat(row['decision_date'])
        end = start + timedelta(days=1)
        matches = [r for r in versions if datetime.fromisoformat(r['valid_from']) <= start and (r['valid_to'] is None or datetime.fromisoformat(r['valid_to']) >= end)]
        if versions and end <= datetime.fromisoformat(versions[0]['valid_from']):
            status = 'PRE_SNAPSHOT_HISTORY_UNKNOWN'
        elif len(matches)==1:
            status = 'MATCHED_CAPTURE_INTERVAL'
    return dict(row,employer_key=matches[0]['employer_key'] if len(matches)==1 else None,employer_history_status=status,fact_time_precision='DATE_ONLY')


def metrics(rows):
    wages = sorted(Decimal(r['annual_wage']) for r in rows if r['annual_wage'] is not None)
    if any(not w.is_finite() for w in wages):
        raise ValueError('nonfinite wage')
    def quantile(p):
        if not wages: return None
        index = (len(wages)-1)*p
        low = int(index); high = min(low+1,len(wages)-1)
        return wages[low]+(wages[high]-wages[low])*(index-low)
    return dict(case_count=len(rows), certified_rate=Decimal(sum(r['case_status'] in ('Certified','Certified - Withdrawn') for r in rows))/len(rows) if rows else None,wage_p50=quantile(Decimal('.5')),wage_p90=quantile(Decimal('.9')))


def compare(a, b, *, abs_tol=Decimal('0.000001'), rel_tol=Decimal('0.000000001')):
    for tolerance in (abs_tol,rel_tol):
        if not math.isfinite(tolerance) or tolerance < 0: raise ValueError('invalid tolerance')
    required = {'scope','manifest_sha256','model_version','registry_sha256','history_sha256'}
    if set(a.get('identity',{})) != required or a['identity'] != b.get('identity') or any(not v for v in a['identity'].values()):
        raise ValueError('source/vintage/window/version identity differs')
    return compare_metrics(a['metrics'],b['metrics'],abs_tol=abs_tol,rel_tol=rel_tol)


def compare_metrics(a,b,*,abs_tol=Decimal('0.000001'),rel_tol=Decimal('0.000000001')):
    for tolerance in (abs_tol,rel_tol):
        if not math.isfinite(tolerance) or tolerance<0:raise ValueError('invalid tolerance')
    for name in ('case_count','certified_rate','wage_p50','wage_p90'):
        x,y = a[name],b[name]
        if name=='case_count':
            if type(x) is not int or type(y) is not int or x<0 or y<0 or x!=y: raise ValueError('case_count must match exactly as integers')
        elif x is None or y is None:
            if x is not y: raise ValueError(name+' NULL mismatch')
        else:
            if isinstance(x,bool) or isinstance(y,bool): raise ValueError('boolean metric')
            x,y = Decimal(str(x)),Decimal(str(y))
            if not x.is_finite() or not y.is_finite(): raise ValueError('nonfinite metric')
            if name=='certified_rate' and not (0<=x<=1 and 0<=y<=1): raise ValueError('invalid certified ratio')
            if abs(x-y)>max(Decimal(str(abs_tol)),Decimal(str(rel_tol))*max(abs(x),abs(y))): raise ValueError(name+' mismatch')
    return {'status':'PASS','metrics':4}
