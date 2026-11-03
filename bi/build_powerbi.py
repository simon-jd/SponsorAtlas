"""Generate reviewable TMDL from the canonical projection; not a PBIX exporter."""
import json

from snapshot import ROOT, connect_snapshot

BI = ROOT / 'bi'
MODEL = BI / 'powerbi/SponsorAtlas.SemanticModel'
TMDL = MODEL / 'definition'

MEASURES = {
    'lca': {
        'Case Count': ('COALESCE(COUNTROWS(lca), 0)', '#,0'),
        'Certified Rate': ('DIVIDE(SUMX(lca, IF(lca[is_certified], 1, 0)), [Case Count])', '0.00%'),
        **{f'Wage P{p}': (f'PERCENTILEX.INC(FILTER(lca, NOT ISBLANK(lca[annual_wage])), lca[annual_wage], {p/100})', '$#,0.00') for p in (50,75,90)},
        **{f'Comparison Wage P{p}': (f'VAR Area = SELECTEDVALUE(PublishedArea[area]) RETURN IF(HASONEVALUE(SOC[soc]), SWITCH(TRUE(), Area = "1:99", CALCULATE([Wage P{p}], REMOVEFILTERS(Metro), REMOVEFILTERS(lca[metro])), LEFT(Area, 2) = "4:" && LEN(Area) = 7, CALCULATE([Wage P{p}], REMOVEFILTERS(Metro), REMOVEFILTERS(lca[metro]), TREATAS({{MID(Area, 3, 5)}}, lca[metro])), BLANK()))', '$#,0.00') for p in (50,75,90)},
        'Wage P75 vs OEWS': ('DIVIDE([Comparison Wage P75], [Comparison OEWS Median])', '0.00x'),
        'Comparison Scope': ('VAR Area = SELECTEDVALUE(PublishedArea[area]) VAR Supported = Area = "1:99" || (LEFT(Area, 2) = "4:" && LEN(Area) = 7) RETURN "SOC " & SELECTEDVALUE(SOC[soc], "select exactly one") & " | Area " & SELECTEDVALUE(PublishedArea[area], "select exactly one") & " | LCA FY " & IF(ISFILTERED(FiscalYear[fiscal_year]), CONCATENATEX(VALUES(FiscalYear[fiscal_year]), FiscalYear[fiscal_year], ", "), "all archived") & " | OEWS survey " & SELECTEDVALUE(SurveyYear[survey_year], "select exactly one") & " | " & SWITCH(TRUE(), NOT HASONEVALUE(SOC[soc]) || NOT HASONEVALUE(PublishedArea[area]), "Choose one shared SOC and published area", NOT Supported, "Not comparable: state/nonmetropolitan/unknown area has no LCA mapping", ISBLANK([Comparison Wage P75]), "No matched non-null LCA wages", ISBLANK([Comparison OEWS Median]), "Benchmark unavailable: choose one survey year; inspect missing/multiple/suppressed estimate", "Matched national/CBSA scope; FY and survey year remain distinct")', ''),
        'Disclaimer': ('"LCA applications are not approvals and do not guarantee sponsorship. LCA 申请不等于获批，也不代表这个岗位一定提供担保。"', ''),
        'Coverage Status': ('"Archived Q4 releases only | Employer identity and history provisional | Unknown mappings retained"', ''),
    },
    'postings': {
        'Active Posting Days': ('SUMX(postings, IF(postings[is_active], 1, 0))', '#,0'),
        'Observed Dates': ('DISTINCTCOUNT(postings[observation_date])', '#,0'),
        'Trend Status': ('IF([Observed Dates] < 2, "One original UTC observation date; trend unavailable", "Active observed posting-days by UTC date")', ''),
    },
    'wages': {
        'OEWS Estimate Count': ('COALESCE(COUNTROWS(wages), 0)', '#,0'),
        'OEWS Median': ('IF([OEWS Estimate Count] = 1, MAX(wages[annual_median_wage]), BLANK())', '$#,0.00'),
        'Comparison OEWS Median': ('VAR Area = SELECTEDVALUE(PublishedArea[area]) RETURN IF(HASONEVALUE(SOC[soc]) && HASONEVALUE(SurveyYear[survey_year]) && (Area = "1:99" || (LEFT(Area, 2) = "4:" && LEN(Area) = 7)), CALCULATE([OEWS Median], REMOVEFILTERS(Metro)))', '$#,0.00'),
        'Reconcile OEWS Median': ('CALCULATE([OEWS Median], REMOVEFILTERS(), wages[soc]="15-1252", wages[area]="1:99", wages[survey_year]=2025)', '$#,0.00'),
        'Benchmark Status': ('IF([OEWS Estimate Count] <> 1, "Select exactly one SOC / published area / survey year", IF(ISBLANK([OEWS Median]), "Suppressed or top-coded: inspect wage_status and median_raw", "One published annual median"))', ''),
    },
}


def main():
    con, _, _ = connect_snapshot(verify_contents=False)
    (TMDL / 'tables').mkdir(parents=True, exist_ok=True)
    (MODEL / 'definition.pbism').write_text(json.dumps({'version':'4.0','settings':{}}, indent=2)+'\n')
    (TMDL / 'database.tmdl').write_text('database\n\tcompatibilityLevel: 1600\n')
    (TMDL / 'model.tmdl').write_text('model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n')
    projections = json.loads((ROOT / 'semantic/projections.json').read_text())
    expressions = [
        'expression SourceMode = "Parquet" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        'expression SnapshotDirectory = "SET_ABSOLUTE_VERIFIED_SNAPSHOT_DIRECTORY" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        'expression SnowflakeServer = "" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        'expression TrialStartedUTC = "" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        'expression TrialExpiresUTC = "" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
    ]
    # The credential dialog owns the key pair. No password/key/token in this model.
    for name, sql in projections.items():
        escaped = sql.replace('{s}', 'ANALYTICS.MARTS').replace('"','""').replace('\n','#(lf)')
        m = f'''let
    Local = () => Parquet.Document(File.Contents(SnapshotDirectory & "/{name}.parquet")),
    Remote = () => let
        Start = DateTimeZone.FromText(TrialStartedUTC),
        Expiry = DateTimeZone.FromText(TrialExpiresUTC),
        Now = DateTimeZone.UtcNow(),
        Valid = Start <= Now and Expiry > Now + #duration(0,0,7,0) and Expiry > Start and Duration.TotalDays(Expiry - Start) <= 30,
        Source = if not Valid or SnowflakeServer = "" then error "Explicit active <=30-day project trial required" else
            Snowflake.Databases(SnowflakeServer, "SPONSORATLAS_XS", [Role="ANALYST", Implementation="2.0", CreateNavigationProperties=false, ConnectionTimeout=60, CommandTimeout=240]),
        Database = Source{{[Name="ANALYTICS",Kind="Database"]}}[Data],
        Data = Value.NativeQuery(Database, "{escaped}", null, [EnableFolding=false])
    in Table.TransformColumnNames(Data, Text.Lower),
    Result = if SourceMode = "Parquet" then Local() else if SourceMode = "Snowflake" then Remote() else error "Choose explicit Parquet or Snowflake source"
in Result'''
        expressions.append('expression Public_'+name+' =\n'+'\n'.join('\t'+line for line in m.splitlines()))
        cols = con.execute(f'DESCRIBE {name}').fetchall()
        text = f'table {name}\n'
        for col, dtype, *_ in cols:
            typ = 'boolean' if dtype=='BOOLEAN' else 'dateTime' if dtype=='DATE' else 'int64' if dtype in ('INTEGER','BIGINT') else 'double' if dtype in ('DOUBLE','FLOAT') or dtype.startswith('DECIMAL') else 'string'
            text += f'\n\tcolumn {col}\n\t\tdataType: {typ}\n\t\tsourceColumn: {col}\n\t\tsummarizeBy: none\n'
            if col in ('case_number','posting_id'):
                text += '\t\tisHidden\n'
        for measure,(formula,fmt) in MEASURES[name].items():
            text += f"\n\tmeasure '{measure}' = {formula}\n"
            if fmt:
                text += f'\t\tformatString: {fmt}\n'
        text += f'\n\tpartition {name} = m\n\t\tmode: import\n\t\tsource = Public_{name}\n'
        (TMDL / 'tables' / f'{name}.tmdl').write_text(text)
    dimensions = {
        'Employer': ('employer','string',['lca','postings']),
        'SOC': ('soc','string',['lca','postings','wages']),
        'Metro': ('metro','string',['lca','postings','wages']),
        'FiscalYear': ('fiscal_year','int64',['lca','postings']),
        'ObservationDate': ('observation_date','dateTime',['postings']),
        'SurveyYear': ('survey_year','int64',['wages']),
        'PublishedArea': ('area','string',['wages']),
    }
    relationships=[]
    for table,(col,typ,facts) in dimensions.items():
        selections=[f'SELECTCOLUMNS({fact}, "{col}", {fact}[{col}])' for fact in facts]
        formula='DISTINCT('+('UNION('+', '.join(selections)+')' if len(facts)>1 else selections[0])+')'
        text=f'''table {table}

\tcolumn {col}
\t\tdataType: {typ}
\t\tisKey
\t\tsourceColumn: [{col}]
\t\tsummarizeBy: none

\tpartition {table} = calculated
\t\tmode: import
\t\tsource = {formula}
'''
        (TMDL/'tables'/f'{table}.tmdl').write_text(text)
        for fact in facts:
            relationships.append(f'''relationship {fact}_{table}
\tfromColumn: {fact}.{col}
\ttoColumn: {table}.{col}
\tfromCardinality: many
\ttoCardinality: one
\tcrossFilteringBehavior: oneDirection
''')
    (TMDL/'relationships.tmdl').write_text('\n'.join(relationships))
    (TMDL/'expressions.tmdl').write_text('\n\n'.join(expressions)+'\n')
    # DAX Query view: execute on the imported model, then export the result.
    query='''// Execute after refresh with no report filters. This query returns the five
// mandatory query scopes; the OEWS filter is deliberately local to that row.
EVALUATE
UNION(
    ROW("metric", "case_count", "value", [Case Count]),
    ROW("metric", "certified_rate", "value", [Certified Rate]),
    ROW("metric", "wage_p75", "value", [Wage P75]),
    ROW("metric", "active_posting_days", "value", [Active Posting Days]),
    ROW("metric", "oews_median", "value",
        CALCULATE([OEWS Median], wages[soc]="15-1252", wages[area]="1:99", wages[survey_year]=2025))
)
ORDER BY [metric]
'''
    (BI/'powerbi/reconcile.dax').write_text(query)
    # Native follow-up query: SQL regression values remain a separate oracle.
    scenarios=[('national','1:99',None),('CBSA35620','4:35620',None),('CBSA35620_FY2025','4:35620',2025),('unsupported_state','2:06',None)]
    native_rows=[]
    for label,area,fy in scenarios:
        filters=['TREATAS({"15-1252"}, SOC[soc])',f'TREATAS({{"{area}"}}, PublishedArea[area])','TREATAS({2025}, SurveyYear[survey_year])']
        if fy is not None:filters.append(f'TREATAS({{{fy}}}, FiscalYear[fiscal_year])')
        row=f'ROW("scenario", "{label}", "p50", [Comparison Wage P50], "p75", [Comparison Wage P75], "p90", [Comparison Wage P90], "oews_median", [Comparison OEWS Median], "ratio", [Wage P75 vs OEWS], "scope", [Comparison Scope])'
        native_rows.append('CALCULATETABLE('+row+', '+', '.join(filters)+')')
    (BI/'powerbi/comparison-checks.dax').write_text('// Pending actual native execution. Compare with LOCAL SQL comparison-scope.json, never import SQL as DAX.\nEVALUATE\nUNION(\n'+',\n'.join(native_rows)+'\n)\n')
    con.close()
    print('TMDL/M/DAX generated; native parse/refresh/render NOT verified')


if __name__ == '__main__':
    main()
