"""scripts.staging_actual_report on a tiny DuckDB: excluded USCIS rows are accounted for."""

import json

import duckdb

from scripts.staging_actual_report import report

LCA = ["FY2024_Q4", "FY2025_Q1", "FY2025_Q2", "FY2025_Q3", "FY2025_Q4"]


def build(path, *, uscis_2023_raw=4):
    c = duckdb.connect(str(path))
    c.execute("create schema RAW_LCA; create schema RAW_USCIS; create schema STAGING")
    for release in LCA:
        c.execute(f"create table RAW_LCA.LCA_{release} as select 1 as x")
    c.execute("create table RAW_USCIS.USCIS_FY2022 as select 1 as x")
    c.execute(
        f"create table RAW_USCIS.USCIS_FY2023 as select range as x from range({uscis_2023_raw})"
    )
    c.execute(
        f"create table STAGING.stg_lca as select unnest({LCA}) as source_release,"
        " 2025 as fiscal_year, false as geo_unresolved, false as soc_unresolved,"
        " 'MATCHED' as geography_status, 'zip' as county_match_rule,"
        " 'CA' as worksite_state, 'SF' as worksite_city"
    )
    # FY2023: 1 released row with a state, 3 blank-address rows that are not released.
    c.execute(
        "create table STAGING.stg_uscis as select * from (values"
        " ('FY2022', 2022, false, false, true),"
        " ('FY2023', 2023, false, false, true),"
        " ('FY2023', 2023, false, true, false),"
        " ('FY2023', 2023, false, true, false),"
        " ('FY2023', 2023, false, true, false))"
        " t(source_release, fiscal_year, employer_name_missing, state_unresolved, released)"
    )
    c.execute(
        "create table STAGING.staging_flag_limits as select * from (values"
        " ('lca', 'geo_unresolved', 0.05), ('lca', 'soc_unresolved', 0.001),"
        " ('uscis', 'employer_name_missing', 0.001), ('uscis', 'state_unresolved', 0.05))"
        " t(source_system, flag, max_share)"
    )
    c.close()


def run(tmp_path, **kw):
    db = tmp_path / "fy.duckdb"  # a file named staging.duckdb would clash with schema STAGING
    build(db, **kw)
    results = tmp_path / "run_results.json"
    results.write_text(json.dumps({"results": [{"unique_id": "t", "status": "pass"}]}))
    return report(db, results, 0)


def test_reconciliation_lists_released_and_excluded_rows_per_release(tmp_path):
    data = run(tmp_path)
    uscis = {r["release"]: r for r in data["reconciliation"] if r["source"] == "uscis"}
    assert uscis["FY2023"] == {
        "source": "uscis", "release": "FY2023", "raw_rows": 4, "staging_rows": 4,
        "released_rows": 1, "excluded_rows": 3,
    }  # fmt: skip
    lca = [r for r in data["reconciliation"] if r["source"] == "lca"]
    assert all(r["excluded_rows"] == 0 and r["released_rows"] == r["raw_rows"] for r in lca)
    assert data["rows_reconcile"] is True
    assert data["excluded_rows"] == {"uscis": {"FY2023": 3}}


def test_gate_share_is_shown_with_and_without_the_exclusion(tmp_path):
    data = run(tmp_path)
    fy2023 = next(u for u in data["uscis_release"] if u["fiscal_year"] == 2023)
    assert fy2023["blank_address_excluded"] == 3
    # Released: 0 / 1 unresolved. Counting the excluded rows: 3 / 4, above the 5% limit.
    assert fy2023["state_unresolved_released_share"] == 0.0
    assert fy2023["state_unresolved_share_if_included"] == 0.75
    assert fy2023["state_unresolved_limit"] == 0.05


def test_raw_rows_lost_before_staging_do_not_reconcile(tmp_path):
    data = run(tmp_path, uscis_2023_raw=5)
    assert data["rows_reconcile"] is False


def test_a_row_neither_released_nor_excluded_does_not_reconcile(tmp_path):
    db = tmp_path / "fy.duckdb"
    build(db)
    with duckdb.connect(str(db)) as c:
        c.execute("update STAGING.stg_uscis set released = null where state_unresolved")
    results = tmp_path / "run_results.json"
    results.write_text(json.dumps({"results": []}))
    assert report(db, results, 0)["rows_reconcile"] is False
