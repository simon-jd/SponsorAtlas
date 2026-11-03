"""The lineage SVG is drawn only from edges present in OpenMetadata API responses."""

import xml.etree.ElementTree as ET

import pytest

from governance.lineage_svg import collect, render_svg

SVG = "{http://www.w3.org/2000/svg}"


def _graph(entity, nodes, edges):
    ids = {fqn: f"id-{fqn}" for fqn in [entity, *nodes]}
    return {
        "entity": {"id": ids[entity], "fullyQualifiedName": entity},
        "nodes": [{"id": ids[n], "fullyQualifiedName": n} for n in nodes],
        "upstreamEdges": [
            {
                "fromEntity": ids[src],
                "toEntity": ids[dst],
                "lineageDetails": {
                    "source": source,
                    "columnsLineage": [
                        {"fromColumns": [f"{src}.{a}"], "toColumn": f"{dst}.{b}"} for a, b in cols
                    ],
                },
            }
            for src, dst, source, cols in edges
        ],
        "downstreamEdges": [],
    }


GRAPHS = {
    "fact": _graph(
        "svc.db.MARTS.fact",
        ["svc.db.RAW.raw", "svc.db.STAGING.stg"],
        [
            ("svc.db.RAW.raw", "svc.db.STAGING.stg", "DbtLineage", [("payload", "case_number")]),
            (
                "svc.db.STAGING.stg",
                "svc.db.MARTS.fact",
                "DbtLineage",
                [("case_number", "case_number"), ("wage", "wage")],
            ),
        ],
    ),
    # The same edge seen from another graph is counted once.
    "stg": _graph(
        "svc.db.STAGING.stg",
        ["svc.db.RAW.raw"],
        [("svc.db.RAW.raw", "svc.db.STAGING.stg", "DbtLineage", [("payload", "case_number")])],
    ),
}
CHAIN = {
    "case": [
        "svc.db.RAW.raw.payload",
        "svc.db.STAGING.stg.case_number",
        "svc.db.MARTS.fact.case_number",
    ]
}


def test_collect_dedupes_tables_and_column_edges():
    tables, table_edges, column_edges = collect(GRAPHS)
    assert tables == ["svc.db.MARTS.fact", "svc.db.RAW.raw", "svc.db.STAGING.stg"]
    assert table_edges == {
        ("svc.db.RAW.raw", "svc.db.STAGING.stg"): ("DbtLineage", 1),
        ("svc.db.STAGING.stg", "svc.db.MARTS.fact"): ("DbtLineage", 2),
    }
    assert ("svc.db.STAGING.stg.wage", "svc.db.MARTS.fact.wage") in column_edges
    assert len(column_edges) == 3


def test_render_is_valid_svg_with_every_table_edge_and_the_chain():
    svg = render_svg(GRAPHS, CHAIN, title="test")
    root = ET.fromstring(svg)
    assert root.tag == SVG + "svg"
    text = " ".join(t.text or "" for t in root.iter(SVG + "text"))
    for label in ["RAW.raw", "STAGING.stg", "MARTS.fact", "2 columns", "1 column", "case_number"]:
        assert label in text
    edges = [p for p in root.iter(SVG + "path") if p.get("class") == "table-edge"]
    assert len(edges) == 2
    steps = [p for p in root.iter(SVG + "path") if p.get("class") == "column-edge"]
    assert len(steps) == 2
    assert render_svg(GRAPHS, CHAIN, title="test") == svg


def test_chain_step_missing_from_the_api_is_refused():
    broken = {"case": [CHAIN["case"][0], "svc.db.MARTS.fact.wage"]}
    with pytest.raises(ValueError, match="not in the API lineage"):
        render_svg(GRAPHS, broken, title="test")
