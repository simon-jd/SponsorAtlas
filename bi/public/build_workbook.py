"""Build the public Tableau workbook (TWBX) from a verified export snapshot.

Output of this generator opened in Tableau Public 2026.2.3 with no error, and every sheet's query returned
rows (see BUILD.md for what that does and does not prove). The workbook it built from the 2026-11-06
snapshot was published on Tableau Public on 2026-11-10.

Tableau Public refuses a workbook whose sources are not extracts, so each fact becomes a Hyper file
with one "Extract"."Extract" table, and each source connects to it the way the World Indicators
sample shipped with the app does. Views never join or blend facts. The five-row Reconciliation sheet
has its own extract, the rows of lca.csv, postings.csv and wages.csv stacked: each metric reads columns
that exist in one file only, and Tableau computes every value from the rows the views use.

Only extracts built from the four public CSV files are packaged. A LOCAL_DUCKDB_DEV snapshot builds
only with --dev, into a file named so it cannot be mistaken for the publishable workbook, and the panel says so.
"""
import argparse
import csv
import hashlib
import json
import sys
import tempfile
import uuid
import xml.dom.minidom
import zipfile
from pathlib import Path

KIND_ACTUAL = "ACTUAL_SNOWFLAKE_PUBLIC_MARTS"
KIND_DEV = "LOCAL_DUCKDB_DEV"
MANIFEST_SCHEMA = "sponsoratlas-bi-public-snapshot/1"
BUILD = "20262.26.0912.1023"  # Tableau Public 2026.2.3, the version the workbook was opened in; see BUILD.md.
SOURCE_BUILD = f"2026.2.3 ({BUILD})"
PACKAGE_DIR = "Data/SponsorAtlas"
TWB_NAME = "SponsorAtlas-public.twb"
PUBLISH_NAME = "SponsorAtlas-public.twbx"
DEV_NAME = "SponsorAtlas-public-DEV-do-not-publish.twbx"
NAMES_RECORD = "names_applied.json"
NAME_LABEL = "most-filed DOL name; grouping unvalidated"
DEFAULT_SOC_PREFIX = "15-1252 "
STATIC_TEXT = "Static snapshot from Snowflake; no automatic refresh"
DEV_TEXT = "DEV BUILD from local DuckDB, not from Snowflake. Static; no automatic refresh. Do not publish."
DISCLAIMER_EN = "LCA applications are not approvals and do not guarantee sponsorship."
DISCLAIMER_ZH = "LCA 申请不等于获批，也不代表这个岗位一定提供担保。"
PERCENTILE_NOTE = ("LCA percentiles are distributions of offered annual wages, not OEWS percentile ranks. "
                   "OEWS: published annual median, survey 2025.")  # fmt: skip
WHO = "Who sponsors data roles?"
PAY = "Offered pay vs benchmark"
WHEN = "When employers file"
RECON = "Reconciliation"
DASHBOARD = "SponsorAtlas public snapshot"
METRICS = ("case_count", "certified_rate", "wage_p75", "active_posting_days", "oews_median")
INTEGER_FORMAT = "n#,##0;-#,##0"
USD_FORMAT = 'c"$"#,##0;-"$"#,##0'
PRECISE_10 = "n0.0000000000;-0.0000000000"
PRECISE_16 = "n0.0000000000000000;-0.0000000000000000"

# name, datatype, role, type, caption
SNAPSHOT_COLUMNS = [
    ("snapshot_kind", "string", "dimension", "nominal", "Snapshot kind"),
    ("snapshot_id", "string", "dimension", "nominal", "Snapshot id"),
]
LCA_COLUMNS = [
    ("case_id", "string", "dimension", "nominal", "Case id"),
    ("fiscal_year", "integer", "dimension", "ordinal", "Fiscal year"),
    ("received_month", "string", "dimension", "nominal", "Received month"),
    ("soc", "string", "dimension", "nominal", "SOC"),
    ("soc_title", "string", "dimension", "nominal", "SOC title"),
    ("metro_cbsa", "string", "dimension", "nominal", "Metro CBSA"),
    ("metro_title", "string", "dimension", "nominal", "Metro"),
    ("state", "string", "dimension", "nominal", "State"),
    ("is_certified", "integer", "measure", "quantitative", "Certified (1/0)"),
    ("annual_wage", "real", "measure", "quantitative", "Offered annual wage"),
    ("employer_id", "string", "dimension", "nominal", "Employer id"),
    ("data_role", "integer", "dimension", "ordinal", "Data role (1/0)"),
    *SNAPSHOT_COLUMNS,
]
LABEL_COLUMN = ("employer_label", "string", "dimension", "nominal", "Employer")
SCHEMAS = {
    "lca.csv": LCA_COLUMNS,
    "postings.csv": [
        ("posting_key", "string", "dimension", "nominal", "Posting key"),
        ("observation_date", "date", "dimension", "ordinal", "Observation date"),
        ("is_active", "integer", "measure", "quantitative", "Active (1/0)"),
        ("employer_id", "string", "dimension", "nominal", "Employer id"),
        ("soc", "string", "dimension", "nominal", "SOC"),
        ("metro_cbsa", "string", "dimension", "nominal", "Metro CBSA"),
        ("employer_mapping_status", "string", "dimension", "nominal", "Employer mapping status"),
        ("replay_kind", "string", "dimension", "nominal", "Replay kind"),
        *SNAPSHOT_COLUMNS,
    ],
    "wages.csv": [
        ("soc", "string", "dimension", "nominal", "SOC"),
        ("soc_title", "string", "dimension", "nominal", "SOC title"),
        ("area", "string", "dimension", "nominal", "Published area"),
        ("survey_year", "integer", "dimension", "ordinal", "Survey year"),
        ("annual_median_wage", "real", "measure", "quantitative", "OEWS annual median"),
        ("median_raw", "string", "dimension", "nominal", "Median as published"),
        ("wage_status", "string", "dimension", "nominal", "Wage status"),
        *SNAPSHOT_COLUMNS,
    ],
    "benchmark.csv": [
        ("soc", "string", "dimension", "nominal", "SOC"),
        ("soc_title", "string", "dimension", "nominal", "SOC title"),
        ("soc_label", "string", "dimension", "nominal", "Occupation"),
        ("metro_cbsa", "string", "dimension", "nominal", "Metro CBSA"),
        ("metro_title", "string", "dimension", "nominal", "Metro"),
        ("lca_cases", "integer", "measure", "quantitative", "LCA cases"),
        ("metro_top25", "integer", "dimension", "ordinal", "Top 25 metro (1/0)"),
        ("lca_p50", "real", "measure", "quantitative", "lca_p50"),
        ("lca_p75", "real", "measure", "quantitative", "lca_p75"),
        ("lca_p90", "real", "measure", "quantitative", "lca_p90"),
        ("oews_median", "real", "measure", "quantitative", "oews_median"),
        ("oews_status", "string", "dimension", "nominal", "OEWS status"),
        ("oews_survey_year", "integer", "dimension", "ordinal", "OEWS survey year"),
        *SNAPSHOT_COLUMNS,
    ],
}
REMOTE_TYPES = {"string": ("129", "Count"), "integer": ("20", "Sum"), "real": ("5", "Sum"), "date": ("133", "Year")}
EXTRACT_NAME = "Extract"
UNION_TABLE_COLUMN = ("Table Name", "string", "dimension", "nominal", "Table name")
# Installed with: pip install --target .tools/hyper-api tableauhyperapi==0.0.26963 (see BUILD.md).
HYPER_API_DIR = Path(__file__).resolve().parents[2] / ".tools" / "hyper-api"
HYPER_TYPES = {"string": "text", "integer": "big_int", "real": "double", "date": "date"}
# The app resolves the feature blocks of its internal XSD from this manifest and validates against the result
# before any upgrade step. The public XSD is flattened, so it cannot catch a mismatch.
# SchemaViewerObjectModel makes <layout> valid without dim-percentage and measure-percentage.
# SetMembershipControl defines zone type-v2; zone also allows any attribute, so neither XSD reports its absence.
# SheetIdentifierTracking goes with simple-id. The ObjectModel* entries stay out: genuine files that declare
# them also carry object-graph, which this XML does not have, so the app should upgrade it to that form itself.
FORMAT_FEATURES = ("SchemaViewerObjectModel", "SetMembershipControl", "SheetIdentifierTracking", "SortTagCleanup",
                   "WindowsPersistSimpleIdentifiers")  # fmt: skip


class Refused(RuntimeError):
    """The snapshot cannot become this workbook."""


def a(value):
    """Escape text for a single-quoted XML attribute."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "&apos;").replace('"', "&quot;"))  # fmt: skip


def t(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tableau_id(prefix, seed):
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    body = "".join(alphabet[(digest >> (5 * index)) % 36] for index in range(28))
    return f"{prefix}.{body}"


def calc_name(seed):
    digits = str(int(hashlib.sha256(seed.encode()).hexdigest(), 16))[:19]
    return f"Calculation_{digits}"


def guid(seed):
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "sponsoratlas:" + seed)).upper() + "}"


def metadata(columns):
    records = []
    for ordinal, column in enumerate(columns):
        name, datatype = column[0], column[1]
        remote, aggregation = REMOTE_TYPES[datatype]
        collation = "\n            <collation flag='0' name='LEN_RUS' />" if datatype == "string" else ""
        records.append(f"""          <metadata-record class='column'>
            <remote-name>{t(name)}</remote-name>
            <remote-type>{remote}</remote-type>
            <local-name>[{t(name)}]</local-name>
            <parent-name>[{EXTRACT_NAME}]</parent-name>
            <remote-alias>{t(name)}</remote-alias>
            <ordinal>{ordinal}</ordinal>
            <local-type>{datatype}</local-type>
            <aggregation>{aggregation}</aggregation>
            <contains-null>true</contains-null>{collation}
          </metadata-record>""")
    return "\n".join(records)


def column_xml(column, indent="      "):
    name, datatype, role, kind, caption = column[:5]
    return f"{indent}<column caption='{a(caption)}' datatype='{datatype}' name='[{a(name)}]' role='{role}' type='{kind}' />"


class Calc:
    def __init__(self, seed, caption, datatype, formula, role="measure", kind="quantitative", number_format=None):
        self.name, self.caption, self.datatype = calc_name(seed), caption, datatype
        self.formula, self.role, self.kind, self.number_format = formula, role, kind, number_format

    @property
    def field(self):
        return f"[{self.name}]"

    @property
    def instance(self):
        if self.role == "measure":
            return f"[usr:{self.name}:qk]"
        return f"[none:{self.name}:nk]"

    def column_xml(self, indent="      "):
        fmt = f" default-format='{a(self.number_format)}'" if self.number_format else ""
        return (f"{indent}<column caption='{a(self.caption)}' datatype='{self.datatype}'{fmt} name='{self.field}' "
                f"role='{self.role}' type='{self.kind}'>\n{indent}  <calculation class='tableau' formula='{a(self.formula)}' />\n"
                f"{indent}</column>")  # fmt: skip

    def instance_xml(self, indent="            "):
        derivation = "User" if self.role == "measure" else "None"
        return (f"{indent}<column-instance column='{self.field}' derivation='{derivation}' name='{self.instance}' "
                f"pivot='key' type='{self.kind}' />")  # fmt: skip


def plain_instance(column, derivation="None", indent="            "):
    name, _, _, kind = column[:4]
    prefix = {"None": "none", "Sum": "sum"}[derivation]
    suffix = {"nominal": "nk", "ordinal": "ok", "quantitative": "qk"}[kind]
    return (f"{indent}<column-instance column='[{a(name)}]' derivation='{derivation}' "
            f"name='[{prefix}:{a(name)}:{suffix}]' pivot='key' type='{kind}' />")  # fmt: skip


class Source:
    def __init__(self, key, caption, filename, columns, calcs=(), union=()):
        self.key, self.caption, self.filename, self.columns = key, caption, filename, list(columns)
        self.calcs, self.union = list(calcs), list(union)
        self.name = tableau_id("federated", key)
        self.connection = tableau_id("hyper", key)
        self.hyper = f"{key}.hyper"

    def column(self, name):
        return next(c for c in self.columns if c[0] == name)

    def ref(self, instance):
        return f"[{self.name}].{instance}"

    def listed(self):
        """The extract's columns, in table order."""
        if not self.union:
            return self.columns
        merged = {}
        for _, columns in self.union:
            for column in columns:
                if merged.setdefault(column[0], column)[1] != column[1]:
                    raise Refused(f"Union column {column[0]} has two types")
        return [*merged.values(), UNION_TABLE_COLUMN]

    def inputs(self):
        return self.union or [(self.filename, self.columns)]

    def datasource_xml(self):
        listed = self.listed()
        columns = "\n".join([column_xml(c) for c in listed] + [c.column_xml() for c in self.calcs])
        return f"""    <datasource caption='{a(self.caption)}' inline='true' name='{self.name}' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='{a(self.key)}' name='{self.connection}'>
            <connection authentication='auth-none' author-locale='en_US' class='hyper' dbname='{PACKAGE_DIR}/{a(self.hyper)}' default-settings='yes' port='' sslmode='' username='tableau_internal_user' />
          </named-connection>
        </named-connections>
        <relation connection='{self.connection}' name='{EXTRACT_NAME}' table='[{EXTRACT_NAME}].[{EXTRACT_NAME}]' type='table' />
        <metadata-records>
{metadata(listed)}
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
{columns}
      <layout dim-ordering='alphabetic' measure-ordering='alphabetic' show-structure='true' />
      <semantic-values>
        <semantic-value key='[Country].[Name]' value='&quot;United States&quot;' />
      </semantic-values>
    </datasource>"""


def title_xml(title, note=None):
    runs = [f"<run bold='true' fontsize='12'>{t(title)}</run>"]
    if note:
        runs += ["<run>Æ&#10;</run>", f"<run fontsize='9'>{t(note)}</run>"]
    return ("      <layout-options>\n        <title>\n          <formatted-text>"
            + "".join(runs) + "</formatted-text>\n        </title>\n      </layout-options>\n")  # fmt: skip


def member_filter(source, instance, member, quoted=False):
    level = instance
    value = f"&quot;{a(member)}&quot;" if quoted else a(member)
    return (f"          <filter class='categorical' column='{source.ref(instance)}'>\n"
            f"            <groupfilter function='member' level='{level}' member='{value}' user:ui-domain='database' "
            "user:ui-enumeration='inclusive' user:ui-marker='enumerate' />\n          </filter>\n")  # fmt: skip


def measure_names_filter(source, instances):
    members = "\n".join(
        f"              <groupfilter function='member' level='[:Measure Names]' member='&quot;{source.ref(i)}&quot;' />"
        for i in instances
    )
    buckets = "\n".join(f"              <bucket>&quot;{source.ref(i)}&quot;</bucket>" for i in instances)
    return (f"          <filter class='categorical' column='{source.ref('[:Measure Names]')}'>\n"
            f"            <groupfilter function='union' user:op='manual'>\n{members}\n            </groupfilter>\n"
            f"          </filter>\n          <manual-sort column='{source.ref('[:Measure Names]')}' direction='ASC'>\n"
            f"            <dictionary>\n{buckets}\n            </dictionary>\n          </manual-sort>\n")  # fmt: skip


def label_pane(pane_id, source, instance, mark="Bar", axis="x"):
    return f"""          <pane id='{pane_id}' selection-relaxation-option='selection-relaxation-allow' {axis}-axis-name='{source.ref(instance)}'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='{mark}' />
            <encodings>
              <text column='{source.ref(instance)}' />
            </encodings>
            <style>
              <style-rule element='mark'>
                <format attr='mark-labels-show' value='true' />
              </style-rule>
            </style>
          </pane>
"""


def worksheet(name, title, note, source, deps, view_body, style, panes, rows, cols):
    deps_xml = "\n".join(deps)
    cols_xml = f"        <cols>{cols}</cols>\n" if cols else "        <cols />\n"
    return f"""    <worksheet name='{a(name)}'>
{title_xml(title, note)}      <table>
        <view>
          <datasources>
            <datasource caption='{a(source.caption)}' name='{source.name}' />
          </datasources>
          <datasource-dependencies datasource='{source.name}'>
{deps_xml}
          </datasource-dependencies>
{view_body}          <aggregation value='true' />
        </view>
{style or "        <style />" + chr(10)}        <panes>
{panes}        </panes>
        <rows>{rows}</rows>
{cols_xml}      </table>
      <simple-id uuid='{guid("sheet:" + name)}' />
    </worksheet>
"""


def plain_pane(mark, encodings=""):
    encoding_xml = f"            <encodings>\n{encodings}            </encodings>\n" if encodings else ""
    return f"""          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='{mark}' />
{encoding_xml}          </pane>
"""


def build_sources(named):
    employer = "employer_label" if named else "employer_id"
    lca_columns = LCA_COLUMNS + [LABEL_COLUMN] if named else LCA_COLUMNS
    lca = Source("lca", "LCA cases", "lca.csv", lca_columns, calcs=[
        Calc("filings", "Filings", "integer", "COUNT([case_id])", number_format=INTEGER_FORMAT),
        Calc("rate", "Certified rate", "real", "FLOAT(SUM([is_certified]))/COUNT([case_id])", number_format="p0.0%"),
        Calc("median", "Median offered wage", "real", "MEDIAN([annual_wage])", number_format=USD_FORMAT),
        Calc("bucket", "Received month", "string",
             'IF [received_month] < "2024-09" THEN "2024-08 or earlier" ELSE [received_month] END',
             role="dimension", kind="nominal"),
    ])  # fmt: skip
    benchmark = Source("benchmark", "Wage benchmark", "benchmark.csv", SCHEMAS["benchmark.csv"], calcs=[
        Calc("p50", "LCA P50 offered", "real", "MAX([lca_p50])", number_format=USD_FORMAT),
        Calc("p75", "LCA P75 offered", "real", "MAX([lca_p75])", number_format=USD_FORMAT),
        Calc("p90", "LCA P90 offered", "real", "MAX([lca_p90])", number_format=USD_FORMAT),
        Calc("oews", "OEWS median", "real", "MAX([oews_median])", number_format=USD_FORMAT),
    ])  # fmt: skip
    postings = Source("postings", "Job postings", "postings.csv", SCHEMAS["postings.csv"])
    wages = Source("wages", "OEWS national", "wages.csv", SCHEMAS["wages.csv"])
    condition = '[soc]="15-1252" AND [area]="1:99" AND [survey_year]=2025'
    reconcile = Source("reconcile", "Reconciliation (row union)", "lca.csv", [], calcs=[
        Calc("case_count", "case_count", "integer", "COUNTD([case_id])", number_format=INTEGER_FORMAT),
        Calc("certified_rate", "certified_rate", "real", "FLOAT(SUM([is_certified]))/COUNT([case_id])",
             number_format=PRECISE_16),
        Calc("wage_p75", "wage_p75", "real", "PERCENTILE([annual_wage],0.75)", number_format=PRECISE_10),
        Calc("active_posting_days", "active_posting_days", "integer", "SUM([is_active])", number_format=INTEGER_FORMAT),
        Calc("oews_median", "oews_median", "real",
             f"IF COUNT(IF {condition} THEN [annual_median_wage] END)=1 "
             f"THEN MAX(IF {condition} THEN [annual_median_wage] END) END",
             number_format=PRECISE_10),
    ], union=[("lca.csv", lca_columns), ("postings.csv", SCHEMAS["postings.csv"]), ("wages.csv", SCHEMAS["wages.csv"])])  # fmt: skip
    return {"lca": lca, "benchmark": benchmark, "postings": postings, "wages": wages, "reconcile": reconcile}, employer


def who_sheet(lca, employer, named):
    filings, rate, median = lca.calcs[0], lca.calcs[1], lca.calcs[2]
    field = lca.column(employer)
    instance = f"[none:{employer}:nk]"
    role_field = lca.column("data_role")
    role_instance = "[none:data_role:ok]"
    order = (f'SUM(IF [{employer}]<>"Other" AND [{employer}]<>"UNKNOWN" THEN [data_role] END)'
             if named else "SUM([data_role])")  # fmt: skip
    deps = [column_xml(c, "            ") for c in (field, role_field, lca.column("case_id"), lca.column("is_certified"),
                                                   lca.column("annual_wage"))]  # fmt: skip
    deps += [c.column_xml("            ") for c in (filings, rate, median)]
    deps += [plain_instance(field), plain_instance(role_field), filings.instance_xml(), rate.instance_xml(), median.instance_xml()]
    top = (f"          <filter class='categorical' column='{lca.ref(instance)}'>\n"
           "            <groupfilter count='25' end='top' function='end' units='records' user:ui-marker='end' user:ui-top-by-field='true'>\n"
           f"              <groupfilter direction='DESC' expression='{a(order)}' function='order' user:ui-marker='order'>\n"
           f"                <groupfilter function='level-members' level='{instance}' user:ui-enumeration='all' user:ui-marker='enumerate' />\n"
           "              </groupfilter>\n            </groupfilter>\n          </filter>\n")  # fmt: skip
    body = (member_filter(lca, role_instance, "1") + top
            + f"          <computed-sort column='{lca.ref(instance)}' direction='DESC' using='{lca.ref(filings.instance)}' />\n"
            + f"          <slices>\n            <column>{lca.ref(role_instance)}</column>\n"
            + f"            <column>{lca.ref(instance)}</column>\n          </slices>\n")  # fmt: skip
    panes = (plain_pane("Bar") + label_pane(1, lca, filings.instance) + label_pane(2, lca, rate.instance)
             + label_pane(3, lca, median.instance))  # fmt: skip
    note = ("Top 25 employers by filings where the job title matches the data_role pattern. "
            + (f"Names: {NAME_LABEL}; everyone else 'Other'." if named
               else "Employers are shown as pseudonymous ids."))  # fmt: skip
    return worksheet(WHO, WHO, note, lca, deps, body, "", panes, lca.ref(instance),
                     f"({lca.ref(filings.instance)} / {lca.ref(rate.instance)} / {lca.ref(median.instance)})")  # fmt: skip


def pay_sheet(benchmark, default_soc):
    measures = benchmark.calcs
    instances = [c.instance for c in measures]
    soc, metro, top, cases = (benchmark.column(n) for n in ("soc_label", "metro_title", "metro_top25", "lca_cases"))
    deps = [column_xml(c, "            ") for c in (soc, metro, top, cases, benchmark.column("lca_p50"), benchmark.column("lca_p75"),
                                                   benchmark.column("lca_p90"), benchmark.column("oews_median"))]  # fmt: skip
    deps += [c.column_xml("            ") for c in measures]
    deps += [plain_instance(soc), plain_instance(metro), plain_instance(top), plain_instance(cases, "Sum")]
    deps += [c.instance_xml() for c in measures]
    body = (measure_names_filter(benchmark, instances)
            + member_filter(benchmark, "[none:soc_label:nk]", default_soc, quoted=True)
            + member_filter(benchmark, "[none:metro_top25:ok]", "1")
            + f"          <computed-sort column='{benchmark.ref('[none:metro_title:nk]')}' direction='DESC' using='{benchmark.ref('[sum:lca_cases:qk]')}' />\n"
            + f"          <slices>\n            <column>{benchmark.ref('[:Measure Names]')}</column>\n"
            + f"            <column>{benchmark.ref('[none:soc_label:nk]')}</column>\n"
            + f"            <column>{benchmark.ref('[none:metro_top25:ok]')}</column>\n          </slices>\n")  # fmt: skip
    encodings = (f"              <color column='{benchmark.ref('[:Measure Names]')}' />\n"
                 f"              <shape column='{benchmark.ref('[:Measure Names]')}' />\n")  # fmt: skip
    note = PERCENTILE_NOTE + " Top 25 metros by LCA cases, each with at least 10 cases."
    return worksheet(PAY, PAY, note, benchmark, deps, body, "", plain_pane("Shape", encodings),
                     benchmark.ref("[none:metro_title:nk]"), benchmark.ref("[Multiple Values]"))  # fmt: skip


def when_sheet(lca):
    filings, bucket = lca.calcs[0], lca.calcs[3]
    year = lca.column("fiscal_year")
    deps = [column_xml(c, "            ") for c in (year, lca.column("case_id"), lca.column("received_month"))]
    deps += [filings.column_xml("            "), bucket.column_xml("            ")]
    deps += [plain_instance(year), filings.instance_xml(), bucket.instance_xml()]
    body = (member_filter(lca, "[none:fiscal_year:ok]", "2025")
            + f"          <slices>\n            <column>{lca.ref('[none:fiscal_year:ok]')}</column>\n          </slices>\n")  # fmt: skip
    note = "FY2025 filings by received month. FY2024 covers Q4 only, so there is no year-over-year comparison."
    return worksheet(WHEN, WHEN, note, lca, deps, body, "", label_pane(1, lca, filings.instance, axis="y"),
                     lca.ref(filings.instance), lca.ref(bucket.instance))  # fmt: skip


def reconcile_sheet(reconcile):
    measures = reconcile.calcs
    instances = [c.instance for c in measures]
    # snapshot_kind and snapshot_id come from the rows, so the sheet states what it was built from.
    used = {"case_id", "is_certified", "annual_wage", "is_active", "soc", "area", "survey_year", "annual_median_wage",
            "snapshot_kind", "snapshot_id"}  # fmt: skip
    merged = []
    for _, columns in reconcile.union:
        merged += [c for c in columns if c[0] in used and c[0] not in {m[0] for m in merged}]
    deps = [column_xml(c, "            ") for c in merged]
    deps += [c.column_xml("            ") for c in reconcile.calcs]
    deps += [c.instance_xml() for c in reconcile.calcs]
    deps += [plain_instance(c) for c in SNAPSHOT_COLUMNS]
    body = (measure_names_filter(reconcile, instances)
            + f"          <slices>\n            <column>{reconcile.ref('[:Measure Names]')}</column>\n          </slices>\n")  # fmt: skip
    formats = "\n".join(f"            <format attr='text-format' field='{reconcile.ref(c.instance)}' value='{a(c.number_format)}' />"
                        for c in measures)  # fmt: skip
    style = f"        <style>\n          <style-rule element='cell'>\n{formats}\n          </style-rule>\n        </style>\n"
    encodings = f"              <text column='{reconcile.ref('[Multiple Values]')}' />\n"
    note = "Computed by Tableau from the packaged extract rows, not stored values. Checked by bi/public/reconcile_public.py."
    rows = (f"({reconcile.ref('[none:snapshot_kind:nk]')} / {reconcile.ref('[none:snapshot_id:nk]')} / "
            f"{reconcile.ref('[:Measure Names]')})")  # fmt: skip
    return worksheet(RECON, RECON, note, reconcile, deps, body, style, plain_pane("Automatic", encodings), rows, None)


def text_zone(zone_id, x, y, w, h, runs):
    return (f"            <zone forceUpdate='true' h='{h}' id='{zone_id}' type-v2='text' w='{w}' x='{x}' y='{y}'>\n"
            f"              <formatted-text>{''.join(runs)}</formatted-text>\n            </zone>\n")  # fmt: skip


def run(text, bold=False, size=None):
    attributes = (" bold='true'" if bold else "") + (f" fontsize='{size}'" if size else "")
    return f"<run{attributes}>{t(text)}</run>"


NEWLINE = "<run>Æ&#10;</run>"


def panel_runs(manifest, manifest_short, kind):
    releases = {}
    for row in manifest["releases"]:
        releases.setdefault(row["fact"], []).append(str(row["release"]))
    lines = [
        run("Freshness and reconciliation", bold=True, size=12),
        run(STATIC_TEXT if kind == KIND_ACTUAL else DEV_TEXT, bold=True),
        run(f"Snapshot time: {manifest['snapshot_time_utc']}"),
        run("Releases covered: " + "; ".join(f"{fact} {', '.join(items)}" for fact, items in sorted(releases.items()))),
        run("Rows: " + ", ".join(f"{name} {manifest['files'][name]['rows']:,}"
                                 for name in ("lca.csv", "postings.csv", "wages.csv", "benchmark.csv"))),
        run(f"Snapshot: {manifest['snapshot_id']}; manifest {manifest_short} ({kind})"),
        run(DISCLAIMER_EN, bold=True),
        run(DISCLAIMER_ZH, bold=True),
    ]  # fmt: skip
    joined = []
    for line in lines:
        joined += [line, NEWLINE]
    return joined[:-1]


def dashboard_xml(sources, manifest, manifest_short, kind):
    benchmark = sources["benchmark"]
    soc = benchmark.column("soc_label")
    filter_field = benchmark.ref("[none:soc_label:nk]")
    legend_field = benchmark.ref("[:Measure Names]")
    header = [run("SponsorAtlas: who files LCAs for data roles, and at what offered pay", bold=True, size=15), NEWLINE,
              run(DISCLAIMER_EN + " " + DISCLAIMER_ZH, bold=True)]  # fmt: skip
    return f"""  <dashboards>
    <dashboard name='{a(DASHBOARD)}'>
      <size maxheight='1600' maxwidth='1200' minheight='1600' minwidth='1200' sizing-mode='fixed' />
      <datasources>
        <datasource caption='{a(benchmark.caption)}' name='{benchmark.name}' />
      </datasources>
      <datasource-dependencies datasource='{benchmark.name}'>
{column_xml(soc, "        ")}
{plain_instance(soc, indent="        ")}
      </datasource-dependencies>
      <zones>
        <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>
{text_zone(2, 0, 0, 100000, 6500, header)}            <zone h='40500' id='3' name='{a(WHO)}' w='50000' x='0' y='6500' />
            <zone h='6500' id='4' mode='dropdown' name='{a(PAY)}' param='{filter_field}' show-all='false' type-v2='filter' w='30000' x='50000' y='6500' />
            <zone h='6500' id='5' name='{a(PAY)}' pane-specification-id='0' param='{legend_field}' type-v2='color' w='20000' x='80000' y='6500' />
            <zone h='34000' id='6' name='{a(PAY)}' w='50000' x='50000' y='13000' />
            <zone h='19000' id='7' name='{a(WHEN)}' w='100000' x='0' y='47000' />
{text_zone(8, 0, 66000, 50000, 34000, panel_runs(manifest, manifest_short, kind))}            <zone h='34000' id='9' name='{a(RECON)}' w='50000' x='50000' y='66000' />
        </zone>
      </zones>
      <simple-id uuid='{guid("dashboard:" + DASHBOARD)}' />
    </dashboard>
  </dashboards>
"""


def window_xml(name, extra_cards=""):
    return f"""    <window class='worksheet' name='{a(name)}'>
      <cards>
        <edge name='left'>
          <strip size='160'>
            <card type='pages' />
            <card type='filters' />
            <card type='marks' />
          </strip>
        </edge>
        <edge name='top'>
          <strip size='2147483647'>
            <card type='columns' />
          </strip>
          <strip size='2147483647'>
            <card type='rows' />
          </strip>
          <strip size='31'>
            <card type='title' />
          </strip>
        </edge>
{extra_cards}      </cards>
      <simple-id uuid='{guid("window:" + name)}' />
    </window>
"""


def windows_xml(sources):
    benchmark = sources["benchmark"]
    right = f"""        <edge name='right'>
          <strip size='160'>
            <card mode='dropdown' param='{benchmark.ref('[none:soc_label:nk]')}' show-all='false' type='filter' />
            <card pane-specification-id='0' param='{benchmark.ref('[:Measure Names]')}' type='color' />
          </strip>
        </edge>
"""
    viewpoints = "\n".join(f"        <viewpoint name='{a(name)}'>\n          <zoom type='entire-view' />\n        </viewpoint>"
                           for name in (WHO, PAY, WHEN, RECON))  # fmt: skip
    return f"""  <windows source-height='30'>
    <window class='dashboard' maximized='true' name='{a(DASHBOARD)}'>
      <viewpoints>
{viewpoints}
      </viewpoints>
      <active id='-1' />
      <simple-id uuid='{guid("window:" + DASHBOARD)}' />
    </window>
{window_xml(WHO)}{window_xml(PAY, right)}{window_xml(WHEN)}{window_xml(RECON)}  </windows>
"""


def workbook_document(datasources, sheets, dashboards, windows):
    features = "\n".join(f"    <{feature} />" for feature in FORMAT_FEATURES)
    return f"""<?xml version='1.0' encoding='utf-8' ?>

<!-- build {BUILD}                               -->
<workbook original-version='18.1' source-build='{SOURCE_BUILD}' source-platform='mac' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
{features}
  </document-format-change-manifest>
  <preferences>
    <preference name='ui.encoding.shelf.height' value='24' />
    <preference name='ui.shelf.height' value='26' />
  </preferences>
  <datasources>
{datasources}
  </datasources>
  <worksheets>
{sheets}  </worksheets>
{dashboards}{windows}</workbook>
"""


def workbook_xml(manifest, manifest_short, default_soc, named=False):
    sources, employer = build_sources(named)
    sheets = (who_sheet(sources["lca"], employer, named) + pay_sheet(sources["benchmark"], default_soc)
              + when_sheet(sources["lca"]) + reconcile_sheet(sources["reconcile"]))  # fmt: skip
    # The postings and wages facts also get their own sources, so each fact is explorable on its own.
    datasources = "\n".join(sources[key].datasource_xml() for key in ("lca", "benchmark", "postings", "wages", "reconcile"))
    return workbook_document(datasources, sheets, dashboard_xml(sources, manifest, manifest_short, manifest["kind"]),
                             windows_xml(sources))  # fmt: skip


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_snapshot(snapshot, *, dev):
    snapshot = Path(snapshot)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise Refused("This snapshot predates the snapshot columns or is not from export_snapshot.py; export it again")
    kind = manifest.get("kind")
    if kind not in (KIND_ACTUAL, KIND_DEV):
        raise Refused(f"Unknown snapshot kind {kind!r}")
    if kind == KIND_DEV and not dev:
        raise Refused("LOCAL_DUCKDB_DEV snapshots are refused for publishing; use --dev for a local test build only")
    if kind == KIND_ACTUAL and dev:
        raise Refused("--dev is only for LOCAL_DUCKDB_DEV snapshots")
    for name in SCHEMAS:
        record = manifest["files"][name]
        if record.get("private") or sha256_file(snapshot / name) != record["sha256"]:
            raise Refused(f"Manifest mismatch for {name}")
    return manifest, hashlib.sha256(manifest_path.read_bytes()).hexdigest()[:12]


def default_soc_label(snapshot):
    with (Path(snapshot) / "benchmark.csv").open(newline="", encoding="utf-8") as stream:
        labels = sorted({row["soc_label"] for row in csv.DictReader(stream)})
    for label in labels:
        if label.startswith(DEFAULT_SOC_PREFIX):
            return label
    if labels:
        return labels[0]
    raise Refused("benchmark.csv is empty")


def package_sources(snapshot, named):
    """The allow-list of packaged files. employer_names_private.csv can never appear here."""
    files = {name: Path(snapshot) / name for name in SCHEMAS}
    if named:
        record_path = Path(snapshot) / NAMES_RECORD
        if not record_path.is_file():
            raise Refused("--names most-filed needs the offline export_snapshot.py --names most-filed step first")
        record = json.loads(record_path.read_text())
        named_path = Path(snapshot) / record["file"]
        if sha256_file(named_path) != record["sha256"]:
            raise Refused("Named LCA file changed after review")
        files["lca.csv"] = named_path
    return files


def hyper_api():
    if HYPER_API_DIR.is_dir() and str(HYPER_API_DIR) not in sys.path:
        sys.path.insert(0, str(HYPER_API_DIR))
    try:
        import tableauhyperapi
    except ImportError as error:
        raise Refused("The Tableau Hyper API is missing; see BUILD.md") from error
    return tableauhyperapi


def write_extracts(sources, files, directory):
    """Write each source's Hyper file from CSV files; returns {packaged name: path}. Sends no telemetry."""
    api = hyper_api()
    table = api.TableName(EXTRACT_NAME, EXTRACT_NAME)

    def definition(name, columns):
        return api.TableDefinition(name, [
            api.TableDefinition.Column(c[0], getattr(api.SqlType, HYPER_TYPES[c[1]])(),
                                       collation="en_US" if c[1] == "string" else None)
            for c in columns])  # fmt: skip

    written = {}
    parameters = {"log_config": "", "default_database_version": "2"}
    with api.HyperProcess(api.Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters=parameters) as hyper:
        for source in sources:
            path = Path(directory) / source.hyper
            with api.Connection(hyper.endpoint, path, api.CreateMode.CREATE_AND_REPLACE) as connection:
                connection.catalog.create_schema(EXTRACT_NAME)
                connection.catalog.create_table(definition(table, source.listed()))
                for filename, columns in source.inputs():
                    stage = api.TableName("public", "stage")
                    connection.catalog.create_table(definition(stage, columns))
                    # COPY binds by position, so a reordered file would silently fill the wrong columns.
                    with Path(files[filename]).open(newline="", encoding="utf-8") as stream:
                        if next(csv.reader(stream), None) != [c[0] for c in columns]:
                            raise Refused(f"{Path(files[filename]).name} does not have the expected columns")
                    literal = api.escape_string_literal(str(files[filename]))
                    connection.execute_command(f"COPY {stage} FROM {literal} WITH (FORMAT CSV, HEADER)")
                    present = {c[0] for c in columns}
                    chosen = []
                    for column in source.listed():
                        if column[0] in present:
                            chosen.append(api.escape_name(column[0]))
                        elif column == UNION_TABLE_COLUMN:
                            chosen.append(api.escape_string_literal(filename))
                        else:
                            chosen.append("NULL")
                    connection.execute_command(f"INSERT INTO {table} SELECT {', '.join(chosen)} FROM {stage}")
                    connection.execute_command(f"DROP TABLE {stage}")
            written[f"{PACKAGE_DIR}/{source.hyper}"] = path
    return written


def build(snapshot, *, dev=False, names="ids", out=None):
    snapshot = Path(snapshot)
    manifest, manifest_short = verify_snapshot(snapshot, dev=dev)
    named = names == "most-filed"
    files = package_sources(snapshot, named)
    xml_text = workbook_xml(manifest, manifest_short, default_soc_label(snapshot), named=named)
    sources, _ = build_sources(named)
    xml.dom.minidom.parseString(xml_text.encode("utf-8"))  # Well-formed, or stop here.
    target = Path(out) if out else snapshot / (DEV_NAME if dev else PUBLISH_NAME)
    partial = target.with_name(target.name + ".partial")
    with tempfile.TemporaryDirectory(dir=target.parent) as scratch:
        extracts = write_extracts(sources.values(), files, scratch)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.writestr(TWB_NAME, xml_text.encode("utf-8"))
            for name, path in extracts.items():
                archive.write(path, name)
    partial.rename(target)
    return target, {"kind": manifest["kind"], "manifest": manifest_short, "names": names,
                    "twbx_bytes": target.stat().st_size, "twb_bytes": len(xml_text.encode("utf-8")),
                    "sha256": sha256_file(target)}  # fmt: skip


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dev", action="store_true", help="Allow a LOCAL_DUCKDB_DEV snapshot; never publish the result")
    parser.add_argument("--names", choices=("ids", "most-filed"), default="ids")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        target, info = build(args.snapshot, dev=args.dev, names=args.names, out=args.out)
    except Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"twbx": str(target), **info}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
