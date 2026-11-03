"""Export RAW -> marts lineage from the running local OpenMetadata API and render it as SVG.

Writes the raw API responses and the rendered SVG to the output directory. Run with
governance/.venv/bin/python -m governance.lineage_export --output docs/evidence/task08-lineage-fy2025
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from governance.catalog import Catalog
from governance.lineage_svg import collect, render_svg
from governance.local_auth import token
from governance.verify_lineage import CHAINS, fetch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["SA_OM_TOKEN"] = token()
    api = Catalog()
    version = api.call("GET", "system/version")
    graphs = fetch(api)
    exported_at = datetime.now(UTC).isoformat(timespec="seconds")
    for name, graph in graphs.items():
        (args.output / f"{name}-lineage.json").write_text(json.dumps(graph, indent=2) + "\n")
    title = (
        f"SponsorAtlas column lineage, OpenMetadata {version['version']} API, {exported_at}"
    )
    (args.output / "lineage.svg").write_text(render_svg(graphs, CHAINS, title=title))
    tables, table_edges, column_edges = collect(graphs)
    summary = {
        "exported_at": exported_at,
        "server": version,
        "tables": len(tables),
        "table_edges": {f"{a} -> {b}": n for (a, b), (_, n) in sorted(table_edges.items())},
        "column_edges": len(column_edges),
        "chains": CHAINS,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("tables", "column_edges")}))


if __name__ == "__main__":
    main()
