"""After Cube exits, independently verify persisted rollups through read-only SQL."""

import argparse
import json
import re
from pathlib import Path

import duckdb

from semantic.scripts.prepare import SETTINGS

ROOT = Path(__file__).resolve().parents[2]


def inspect(kind):
    work = ROOT / ".tools/semantic" / kind
    oracle = json.loads((work / "oracle.json").read_text())
    builds = []
    with duckdb.connect(
        str(work / "scratch/cube.duckdb"), read_only=True, config=SETTINGS
    ) as connection:
        tables = connection.execute(
            "select table_name from information_schema.tables where table_schema='semantic_preagg' and table_type='BASE TABLE' order by table_name"
        ).fetchall()
        assert len(tables) == 2, tables
        for (name,) in tables:
            assert re.fullmatch(r"(lca|postings)_counts_[a-z0-9_]+", name)
            result = connection.execute("select * from semantic_preagg." + name)
            columns = [d[0] for d in result.description]
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            if name.startswith("lca_"):
                assert len(rows) == len(oracle["fiscal_groups"])
                for expected in oracle["fiscal_groups"]:
                    row = next(
                        row for row in rows if row["lca__fiscal_year"] == expected["fiscal_year"]
                    )
                    assert row["lca__case_count"] == expected["case_count"]
                    assert (
                        abs(
                            row["lca__certified_count"] / row["lca__case_count"]
                            - expected["certified_rate"]
                        )
                        < 1e-12
                    )
            else:
                assert sum(row["postings__count"] for row in rows) == oracle["postings"]
            builds.append({"table_name": name, "rows": rows})
    (work / "preagg-evidence.json").write_text(json.dumps(builds, indent=2) + "\n")
    print(
        json.dumps(
            {
                "kind": kind,
                "built_rollups": len(builds),
                "rollup_rows": sum(len(b["rows"]) for b in builds),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["fixture", "actual"])
    inspect(parser.parse_args().kind)
