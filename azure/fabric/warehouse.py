"""Fixed SQL files only; transactionally rebuild the dedicated Warehouse marts."""
from pathlib import Path
import hashlib
import re

HERE=Path(__file__).resolve().parent
TABLES=('fact_lca_case','dim_employer','dim_occupation','dim_geography','dim_date','port_identity')


def sql_digest():
    digest=hashlib.sha256()
    for name in ('ddl.sql','dimensions.sql','fact-select.sql','checks.sql','input-checks.sql','verify-fabric.sql','verify-snowflake.sql'):
        digest.update(name.encode()+b'\0'+(HERE/name).read_bytes()+b'\0')
    return digest.hexdigest()


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',value):
        raise ValueError('unsafe SQL identifier')
    return value


def statements(text):
    result=[]; part=[]; i=0; quote=None; comment=None
    while i<len(text):
        pair=text[i:i+2]; char=text[i]
        if comment:
            if comment=='line' and char=='\n': comment=None;part.append('\n')
            elif comment=='block' and pair=='*/': comment=None;i+=1;part.append(' ')
        elif quote:
            part.append(char)
            if char==quote:
                if i+1<len(text) and text[i+1]==quote: part.append(quote);i+=1
                else: quote=None
        elif pair in ('--','/*'): comment='line' if pair=='--' else 'block';i+=1
        elif char in ("'",'"','['): quote=']' if char=='[' else char;part.append(char)
        elif char==';':
            if ''.join(part).strip(): result.append(''.join(part).strip())
            part=[]
        else: part.append(char)
        i+=1
    if quote or comment=='block': raise ValueError('unterminated SQL literal/comment')
    if ''.join(part).strip(): result.append(''.join(part).strip())
    return result


def sql_file(name,lakehouse):
    return (HERE/name).read_text().replace('{{lakehouse}}',identifier(lakehouse))


def assert_checks(cursor,name,lakehouse):
    for index,sql in enumerate(statements(sql_file(name,lakehouse))):
        row=cursor.execute(sql).fetchone()
        if row is None or row[0]!=0:
            raise ValueError(f'{name} check {index+1} failed')


def rebuild(connection,lakehouse,expected_identity,*,bootstrap=False,manifest=None,accept=None):
    cursor=connection.cursor()
    cursor.execute('BEGIN TRANSACTION')
    try:
        # SQL endpoint synchronization must already have published the READY row.
        identity=cursor.execute(f'SELECT scope,manifest_sha256,model_version,registry_sha256,history_sha256 FROM {identifier(lakehouse)}.dbo.port_identity').fetchall()
        if len(identity)!=1 or list(identity[0])!=[expected_identity[k] for k in ('scope','manifest_sha256','model_version','registry_sha256','history_sha256')]:
            raise ValueError('Lakehouse readiness/identity mismatch')
        assert_checks(cursor,'input-checks.sql',lakehouse)
        if manifest is not None:
            # SQL analytics endpoint synchronization can lag Delta writes. READY
            # alone is insufficient: require every visible relation's full count.
            observed=cursor.execute(f'SELECT source_release,source_sha256,COUNT_BIG(*) FROM {identifier(lakehouse)}.dbo.port_lca GROUP BY source_release,source_sha256').fetchall()
            if {tuple(r) for r in observed}!={(a['release'],a['original_sha256'],a['rows']) for a in manifest['archives']}:raise ValueError('SQL endpoint archive synchronization incomplete')
            for ref in manifest['references']:
                count=cursor.execute(f"SELECT COUNT_BIG(*) FROM {identifier(lakehouse)}.dbo.port_{identifier(ref['table'])}").fetchone()[0]
                if count!=ref['rows']:raise ValueError('SQL endpoint reference synchronization incomplete')
        elif expected_identity['scope']=='LCA_FY2024_Q4+FY2025_Q4':
            raise ValueError('native load requires full immutable input manifest')
        if bootstrap:
            for statement in statements(sql_file('ddl.sql',lakehouse)): cursor.execute(statement)
        else:
            # Repeat same immutable batch: no duplicate append or stale survivors.
            for table in TABLES: cursor.execute('DELETE FROM MARTS.'+table)
        for statement in statements(sql_file('dimensions.sql',lakehouse)): cursor.execute(statement)
        fact=sql_file('fact-select.sql',lakehouse)
        # T-SQL CTE precedes INSERT, unlike a SELECT-only subquery.
        pos=fact.index('\nSELECT w.case_number')
        fact=fact[:pos]+'\nINSERT INTO MARTS.fact_lca_case'+fact[pos:]
        cursor.execute(fact)
        cursor.execute(f'INSERT INTO MARTS.port_identity SELECT * FROM {identifier(lakehouse)}.dbo.port_identity')
        assert_checks(cursor,'checks.sql',lakehouse)
        if accept is None and expected_identity['scope']=='LCA_FY2024_Q4+FY2025_Q4':
            raise ValueError('native replacement requires acceptance before commit')
        accepted=accept(cursor) if accept is not None else None
        cursor.execute('COMMIT')
        return accepted
    except BaseException:
        cursor.execute('ROLLBACK')
        raise
    finally:
        cursor.close()
