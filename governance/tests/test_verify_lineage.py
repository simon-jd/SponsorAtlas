"""The asserted API chains cover the full-year catalogs and the ZIP/city county fill."""

from governance.verify_lineage import CHAINS, FACTS, STAGING, TABLES
from scripts.canonical_paths import FACTS_DB, STAGING_DB


def test_chains_point_at_the_canonical_full_year_catalogs():
    assert STAGING.endswith("." + STAGING_DB.stem)
    assert FACTS.endswith("." + FACTS_DB.stem)
    for path in CHAINS.values():
        for column in path:
            assert ".actual." not in column and ".actual-files." not in column


def test_worksite_zip_reaches_the_case_candidates():
    assert CHAINS["public_lca_worksite_zip5"] == [
        STAGING + ".RAW_LCA.LCA_FY2025_Q4.payload_json",
        STAGING + ".STAGING.stg_lca.worksite_zip5",
        FACTS + ".FACTS_INPUT.lca.worksite_zip5",
        FACTS + ".INTERMEDIATE.int_lca_case_candidates.worksite_zip5",
    ]


def test_zip_and_city_crosswalks_feed_the_county_fips_chain():
    for seed in ("zcta_county_fips", "place_county_fips"):
        assert CHAINS[f"public_lca_county_from_{seed}"] == [
            STAGING + f".STAGING.{seed}.county_fips",
            STAGING + ".STAGING.stg_lca.county_fips",
            FACTS + ".FACTS_INPUT.lca.county_fips",
            FACTS + ".INTERMEDIATE.int_lca_case_candidates.county_fips",
        ]


def test_every_chain_touches_a_table_whose_lineage_is_fetched():
    fetched = set(TABLES.values())
    for path in CHAINS.values():
        tables = {column.rsplit(".", 1)[0] for column in path}
        assert tables & fetched, path
