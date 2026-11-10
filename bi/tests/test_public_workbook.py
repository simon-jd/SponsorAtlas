"""Structure and refusal checks on the generated TWBX. Whether it opens is checked in the real app (BUILD.md)."""
import csv
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

PUBLIC = Path(__file__).resolve().parents[1] / "public"
sys.path.insert(0, str(PUBLIC))
import build_workbook as bw  # noqa: E402

try:
    bw.hyper_api()
except bw.Refused as error:
    pytest.skip(str(error), allow_module_level=True)

SECRET = "Secret Source Employer Inc"
USER = "{http://www.tableausoftware.com/xml/user}"
SNAPSHOT_ID = "20261106T030000Z-1a2b3c4d"
EXTRACTS = ("lca", "benchmark", "postings", "wages", "reconcile")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream, lineterminator="\n").writerows(rows)


def make_snapshot(root, kind=bw.KIND_ACTUAL):
    root.mkdir()
    schema = {name: [column[0] for column in columns] for name, columns in bw.SCHEMAS.items()}
    write(root / "lca.csv", [schema["lca.csv"],
          ["I-1", 2025, "2025-03", "15-1252", "Software Developers", "35620", "New York-Newark-Jersey City, NY-NJ",
           "NY", 1, "156383.63", "emp_aaaaaaaaaaaa", 1, kind, SNAPSHOT_ID]])  # fmt: skip
    write(root / "postings.csv", [schema["postings.csv"],
          ["abc", "2026-10-24", 1, "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNRESOLVED", "LOCAL_ARCHIVE_REPLAY", kind, SNAPSHOT_ID]])  # fmt: skip
    write(root / "wages.csv", [schema["wages.csv"],
          ["15-1252", "Software Developers", "1:99", 2025, "135980.00", "135980", "REPORTED", kind, SNAPSHOT_ID]])  # fmt: skip
    write(root / "benchmark.csv", [schema["benchmark.csv"],
          ["15-1252", "Software Developers", "15-1252 Software Developers", "35620", "New York-Newark-Jersey City, NY-NJ",
           12, 1, 1.5, 2.5, 3.5, "150000.00", "REPORTED", 2025, kind, SNAPSHOT_ID]])  # fmt: skip
    write(root / "employer_names_private.csv", [["employer_id", "employer_id_full", "source_employer_name", "cases"],
          ["emp_aaaaaaaaaaaa", "emp_a", SECRET, 12]])  # fmt: skip
    files = {name: {"rows": 1, "sha256": sha(root / name), "private": False} for name in bw.SCHEMAS}
    files["employer_names_private.csv"] = {"rows": 1, "sha256": sha(root / "employer_names_private.csv"), "private": True}
    manifest = {"schema": bw.MANIFEST_SCHEMA, "kind": kind, "snapshot_id": SNAPSHOT_ID, "snapshot_time_utc": "2026-11-06T03:00:00Z", "files": files,
                "releases": [{"fact": "LCA", "release": "FY2025_Q2", "row_count": 1},
                             {"fact": "OEWS", "release": "FY2025", "row_count": 1},
                             {"fact": "POSTINGS", "release": "LOCAL_ARCHIVE_REPLAY 2026-10-24", "row_count": 1}]}  # fmt: skip
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def workbook(path):
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read(bw.TWB_NAME))


def extract_rows(twbx, key, directory):
    """(column names, rows) of one packaged extract, read back through Hyper."""
    api = bw.hyper_api()
    with zipfile.ZipFile(twbx) as archive:
        path = archive.extract(f"{bw.PACKAGE_DIR}/{key}.hyper", directory)
    with api.HyperProcess(api.Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_config": ""}) as hyper:
        with api.Connection(hyper.endpoint, path, api.CreateMode.NONE) as connection:
            table = api.TableName(bw.EXTRACT_NAME, bw.EXTRACT_NAME)
            columns = [column.name.unescaped for column in connection.catalog.get_table_definition(table).columns]
            return columns, connection.execute_list_query(f"SELECT * FROM {table}")


def text_of(root):
    return "".join(run.text or "" for run in root.iter("run"))


@pytest.fixture
def actual(tmp_path):
    return make_snapshot(tmp_path / "actual")


@pytest.fixture
def built(actual):
    return bw.build(actual)[0]


def test_dev_kind_refused_for_publishing(tmp_path):
    snapshot = make_snapshot(tmp_path / "dev", bw.KIND_DEV)
    with pytest.raises(bw.Refused, match="LOCAL_DUCKDB_DEV"):
        bw.build(snapshot)
    assert not (snapshot / bw.PUBLISH_NAME).exists()


def test_dev_build_is_named_and_labelled_as_dev(tmp_path):
    snapshot = make_snapshot(tmp_path / "dev", bw.KIND_DEV)
    target, info = bw.build(snapshot, dev=True)
    assert target.name == bw.DEV_NAME and info["kind"] == bw.KIND_DEV
    root = workbook(target)
    assert bw.DEV_TEXT in text_of(root) and bw.STATIC_TEXT not in text_of(root)
    assert bw.KIND_DEV in text_of(root)


def test_reconciliation_identity_comes_from_the_rows_and_is_not_typed(tmp_path, actual):
    for snapshot in (actual, make_snapshot(tmp_path / "dev", bw.KIND_DEV)):
        target = bw.build(snapshot, dev=snapshot.name == "dev")[0]
        root = workbook(target)
        formulas = [item.get("formula") or "" for item in root.iter("calculation")]
        assert not [f for f in formulas if bw.KIND_ACTUAL in f or bw.KIND_DEV in f or SNAPSHOT_ID in f]
        sheet = next(item for item in root.find("worksheets") if item.get("name") == bw.RECON)
        rows = sheet.find("table/rows").text
        assert "[none:snapshot_kind:nk]" in rows and "[none:snapshot_id:nk]" in rows
        instances = {c.get("name") for c in sheet.iter("column-instance")}
        assert {"[none:snapshot_kind:nk]", "[none:snapshot_id:nk]"} <= instances


def test_a_snapshot_without_the_snapshot_columns_is_refused(actual):
    manifest = json.loads((actual / "manifest.json").read_text())
    del manifest["schema"]
    (actual / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(bw.Refused, match="export it again"):
        bw.build(actual)


def test_actual_build_states_static_snapshot_and_refuses_dev_flag(actual):
    with pytest.raises(bw.Refused, match="--dev"):
        bw.build(actual, dev=True)
    target = bw.build(actual)[0]
    assert target.name == bw.PUBLISH_NAME
    text = text_of(workbook(target))
    assert bw.STATIC_TEXT in text and "2026-11-06T03:00:00Z" in text
    assert "FY2025_Q2" in text and "LOCAL_ARCHIVE_REPLAY 2026-10-24" in text
    assert hashlib.sha256((actual / "manifest.json").read_bytes()).hexdigest()[:12] in text
    assert SNAPSHOT_ID in text


def test_changed_csv_refused(actual):
    with (actual / "lca.csv").open("a") as stream:
        stream.write("I-2,2025\n")
    with pytest.raises(bw.Refused, match="Manifest mismatch"):
        bw.build(actual)


def test_a_reordered_csv_is_refused_instead_of_filling_the_wrong_columns(actual):
    # The manifest digest must still match, so reorder the reviewed names file, which has its own digest.
    named = actual / "lca_most_filed.csv"
    header = [column[0] for column in bw.LCA_COLUMNS] + ["employer_label"]
    write(named, [list(reversed(header))])
    (actual / bw.NAMES_RECORD).write_text(json.dumps({"file": named.name, "sha256": sha(named)}))
    with pytest.raises(bw.Refused, match="expected columns"):
        bw.build(actual, names="most-filed", out=actual / "named.twbx")


def test_required_disclaimer_is_on_the_panel(built):
    text = text_of(workbook(built))
    assert bw.DISCLAIMER_EN in text and bw.DISCLAIMER_ZH in text


def test_package_is_an_allow_list_without_private_names(built, tmp_path):
    with zipfile.ZipFile(built) as archive:
        names = set(archive.namelist())
        assert names == {bw.TWB_NAME, *(f"{bw.PACKAGE_DIR}/{key}.hyper" for key in EXTRACTS)}
        assert SECRET.encode() not in archive.read(bw.TWB_NAME)
        assert not archive.testzip()
    # Hyper files may compress strings, so read the values back instead of searching the bytes.
    for key in EXTRACTS:
        _, rows = extract_rows(built, key, tmp_path / key)
        assert not [row for row in rows if SECRET in row]


def test_root_matches_tableau_saved_structure(built):
    root = workbook(built)
    assert root.tag == "workbook" and root.get("version") == "18.1"
    assert root.get("source-platform") == "mac" and root.get("source-build") == bw.SOURCE_BUILD
    with zipfile.ZipFile(built) as archive:
        assert f"<!-- build {bw.BUILD}".encode() in archive.read(bw.TWB_NAME)
    children = [child.tag for child in root]
    assert children == ["document-format-change-manifest", "preferences", "datasources", "worksheets", "dashboards", "windows"]


def test_manifest_declares_the_formats_the_xml_is_written_in(built):
    """The app validates against an XSD resolved from this manifest; the public XSD cannot catch a mismatch."""
    root = workbook(built)
    declared = {child.tag for child in root.find("document-format-change-manifest")}
    assert declared == set(bw.FORMAT_FEATURES)
    layouts = list(root.iter("layout"))
    assert len(layouts) == 5 and "SchemaViewerObjectModel" in declared
    assert not [item for item in layouts if item.get("dim-percentage") or item.get("measure-percentage")]
    zones = list(root.iter("zone"))
    assert "SetMembershipControl" in declared
    assert [zone for zone in zones if zone.get("type-v2")] and not [zone for zone in zones if zone.get("type")]
    assert "SheetIdentifierTracking" in declared
    for item in [*root.find("worksheets"), *root.find("dashboards"), *root.find("windows")]:
        assert item.find("simple-id") is not None, item.get("name")
    # Declaring these would claim the object-graph form, which this XML does not have.
    assert not [feature for feature in declared if feature.startswith("ObjectModel")]
    assert not list(root.iter("object-graph"))


def test_every_source_connects_to_a_packaged_extract(built):
    # Tableau Public refuses a source that is not an extract, with error 3c242d89.
    root = workbook(built)
    sources = {ds.get("caption"): ds for ds in root.find("datasources")}
    assert len(sources) == 5
    with zipfile.ZipFile(built) as archive:
        packaged = set(archive.namelist())
    for ds in sources.values():
        assert ds.get("name").startswith("federated.") and ds.get("inline") == "true"
        connection = ds.find("connection")
        assert connection.get("class") == "federated"
        inner = connection.find("named-connections/named-connection/connection")
        assert inner.get("class") == "hyper" and inner.get("dbname") in packaged
        relation = connection.find("relation")
        assert relation.get("table") == "[Extract].[Extract]" and relation.get("type") == "table"
        assert connection.findall("metadata-records/metadata-record[@class='column']")
    assert not [item for item in root.iter("connection") if item.get("class") == "textscan"]


def test_schema_columns_match_the_csv_headers_and_the_extract(actual, built, tmp_path):
    root = workbook(built)
    for caption, key in (("LCA cases", "lca"), ("Wage benchmark", "benchmark"),
                         ("Job postings", "postings"), ("OEWS national", "wages")):  # fmt: skip
        ds = next(item for item in root.find("datasources") if item.get("caption") == caption)
        declared = [item.find("remote-name").text for item in ds.findall("connection/metadata-records/metadata-record")]
        with (actual / f"{key}.csv").open(newline="") as stream:
            header = next(csv.reader(stream))
        assert declared == header
        assert extract_rows(built, key, tmp_path / key)[0] == header


def test_extracts_hold_exactly_the_csv_rows_and_empty_means_null(actual, built, tmp_path):
    for key in ("lca", "benchmark", "postings", "wages"):
        with (actual / f"{key}.csv").open(newline="") as stream:
            source = list(csv.DictReader(stream))
        columns, rows = extract_rows(built, key, tmp_path / key)
        assert len(rows) == len(source)
        for expected, row in zip(source, rows):
            for name, value in zip(columns, row):
                assert (value is None) == (expected[name] == ""), name
                if isinstance(value, str):
                    assert value == expected[name]


def test_no_joins_or_blends_between_facts(built):
    root = workbook(built)
    assert not [item for item in root.iter("relation") if item.get("type") == "join"]
    for sheet in root.find("worksheets"):
        assert len(sheet.findall("table/view/datasources/datasource")) == 1, sheet.get("name")
        assert len(sheet.findall("table/view/datasource-dependencies")) == 1, sheet.get("name")


def test_reconciliation_extract_only_stacks_the_three_facts(actual, built, tmp_path):
    columns, rows = extract_rows(built, "reconcile", tmp_path)
    assert columns[-1] == "Table Name"
    tables = [row[-1] for row in rows]
    expected = []
    for filename in ("lca.csv", "postings.csv", "wages.csv"):
        with (actual / filename).open(newline="") as stream:
            expected += [filename] * (sum(1 for _ in csv.reader(stream)) - 1)
    assert tables == expected
    # A column one file does not have stays NULL on that file's rows, so nothing is joined or filled in.
    at = columns.index("case_id"), columns.index("posting_key")
    for row in rows:
        assert (row[at[0]] is None) == (row[-1] != "lca.csv")
        assert (row[at[1]] is None) == (row[-1] != "postings.csv")


def test_reconciliation_sheet_has_five_full_precision_measures(built):
    root = workbook(built)
    sheet = next(item for item in root.find("worksheets") if item.get("name") == bw.RECON)
    captions = {item.get("caption"): item for item in sheet.findall("table/view/datasource-dependencies/column")}
    for metric in bw.METRICS:
        assert metric in captions
    members = sheet.findall("table/view/filter/groupfilter/groupfilter")
    assert len(members) == 5
    formats = {item.get("field").split(".")[-1]: item.get("value") for item in sheet.iter("format") if item.get("attr") == "text-format"}
    decimals = lambda fmt: len(fmt.split(";")[0].split(".")[1]) if "." in fmt else 0  # noqa: E731
    for metric in ("certified_rate", "wage_p75", "oews_median"):
        field = captions[metric].get("name")
        assert decimals(formats[f"[usr:{field[1:-1]}:qk]"]) >= 10, metric
    formulas = {caption: item.find("calculation").get("formula") for caption, item in captions.items() if item.find("calculation") is not None}
    assert formulas["wage_p75"] == "PERCENTILE([annual_wage],0.75)"
    assert formulas["case_count"] == "COUNTD([case_id])"


def test_views_use_the_right_scope(built):
    root = workbook(built)
    sheets = {item.get("name"): item for item in root.find("worksheets")}
    assert set(sheets) == {bw.WHO, bw.PAY, bw.WHEN, bw.RECON}
    top = next(item for item in sheets[bw.WHO].iter("groupfilter") if item.get("function") == "end")
    assert top.get("count") == "25" and top.get("end") == "top"
    fiscal = sheets[bw.WHEN].find("table/view/filter/groupfilter")
    assert fiscal.get("member") == "2025"
    soc = next(item for item in sheets[bw.PAY].iter("groupfilter") if "soc_label" in (item.get("level") or ""))
    assert soc.get("member") == '"15-1252 Software Developers"'
    assert "not OEWS percentile ranks" in text_of(sheets[bw.PAY])
    assert "no year-over-year" in text_of(sheets[bw.WHEN])


def test_percentile_measures_are_never_averaged_or_summed(built):
    root = workbook(built)
    ds = next(item for item in root.find("datasources") if item.get("caption") == "Wage benchmark")
    formulas = [item.find("calculation").get("formula") for item in ds.findall("column") if item.find("calculation") is not None]
    assert formulas == ["MAX([lca_p50])", "MAX([lca_p75])", "MAX([lca_p90])", "MAX([oews_median])"]


def test_dashboard_zones_resolve_and_are_layout_basic(built):
    root = workbook(built)
    dashboard = root.find("dashboards/dashboard")
    outer = dashboard.find("zones/zone")
    assert outer.get("type-v2") == "layout-basic"
    sheet_names = {item.get("name") for item in root.find("worksheets")}
    zones = list(outer.iter("zone"))
    named = {zone.get("name") for zone in zones if zone.get("name")}
    assert named == sheet_names
    ids = [zone.get("id") for zone in zones]
    assert len(ids) == len(set(ids))
    size = dashboard.find("size")
    assert size.get("minwidth") == size.get("maxwidth") and size.get("minheight") == size.get("maxheight")
    window = next(item for item in root.find("windows") if item.get("class") == "dashboard")
    assert {item.get("name") for item in window.findall("viewpoints/viewpoint")} == sheet_names


def test_most_filed_needs_the_offline_step_then_uses_labels(actual, tmp_path):
    with pytest.raises(bw.Refused, match="offline"):
        bw.build(actual, names="most-filed")
    named = actual / "lca_most_filed.csv"
    header = [column[0] for column in bw.LCA_COLUMNS] + ["employer_label"]
    write(named, [header, ["I-1", 2025, "2025-03", "15-1252", "Software Developers", "35620", "NY Metro", "NY", 1,
                           "156383.63", "emp_aaaaaaaaaaaa", 1, bw.KIND_ACTUAL, SNAPSHOT_ID, "Reviewed Label"]])  # fmt: skip
    (actual / bw.NAMES_RECORD).write_text(json.dumps({"file": named.name, "sha256": sha(named)}))
    target, info = bw.build(actual, names="most-filed", out=actual / "named.twbx")
    assert info["names"] == "most-filed"
    root = workbook(target)
    assert bw.NAME_LABEL in text_of(root)
    ds = next(item for item in root.find("datasources") if item.get("caption") == "LCA cases")
    assert "employer_label" in [item.find("remote-name").text for item in ds.findall("connection/metadata-records/metadata-record")]
    columns, rows = extract_rows(target, "lca", tmp_path)
    assert rows[0][columns.index("employer_label")] == "Reviewed Label"
    named.write_text("changed\n")
    with pytest.raises(bw.Refused, match="changed after review"):
        bw.build(actual, names="most-filed", out=actual / "named2.twbx")
