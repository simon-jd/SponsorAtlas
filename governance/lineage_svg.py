"""Render OpenMetadata lineage API responses as a static SVG.

Only tables and column edges that appear in the API JSON are drawn; a requested column
chain with a step the API does not return is refused instead of drawn. No layout input
other than the graph itself (tables are layered by their longest upstream path).
"""

from __future__ import annotations

import itertools
from html import escape

NODE_W, NODE_H, GAP_X, GAP_Y, PAD = 250, 46, 90, 22, 30
CHAIN_W, CHAIN_H, CHAIN_GAP = 230, 40, 40


def collect(graphs: dict) -> tuple[list[str], dict, set]:
    """Tables, table edges {(from, to): (source, column count)} and column edges."""
    tables, table_edges, column_edges = set(), {}, set()
    for graph in graphs.values():
        names = {e["id"]: e["fullyQualifiedName"] for e in [graph["entity"], *graph["nodes"]]}
        tables.update(names.values())
        for edge in graph["upstreamEdges"] + graph["downstreamEdges"]:
            src, dst = names[edge["fromEntity"]], names[edge["toEntity"]]
            details = edge.get("lineageDetails", {})
            columns = details.get("columnsLineage", [])
            for column in columns:
                for upstream in column.get("fromColumns", []):
                    column_edges.add((upstream, column["toColumn"]))
            table_edges[src, dst] = (details.get("source", ""), len(columns))
    return sorted(tables), table_edges, column_edges


def _layers(tables: list[str], edges) -> dict[str, int]:
    layer = dict.fromkeys(tables, 0)
    for _ in tables:  # longest-path layering; the graph is a DAG
        for src, dst in edges:
            layer[dst] = max(layer[dst], layer[src] + 1)
    return layer


def _short(fqn: str) -> tuple[str, str]:
    service, database, *rest = fqn.split(".")
    return ".".join(rest), f"{service} / {database}"


def _text(x, y, value, size=12, weight="normal", anchor="start", fill="#1f2933"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}" fill="{fill}">{escape(value)}</text>'
    )


def render_svg(graphs: dict, chains: dict[str, list[str]], *, title: str) -> str:
    tables, table_edges, column_edges = collect(graphs)
    for name, path in chains.items():
        for step in itertools.pairwise(path):
            if step not in column_edges:
                raise ValueError(f"{name}: {step[0]} -> {step[1]} is not in the API lineage")
    layer = _layers(tables, table_edges)
    rows: dict[int, list[str]] = {}
    for table in tables:
        rows.setdefault(layer[table], []).append(table)
    pos = {}
    for depth, members in rows.items():
        for row, table in enumerate(members):
            pos[table] = (PAD + depth * (NODE_W + GAP_X), PAD + 40 + row * (NODE_H + GAP_Y))
    graph_bottom = max(y for _, y in pos.values()) + NODE_H + 50
    longest_chain = max((len(p) for p in chains.values()), default=0)
    width = max(
        PAD * 2 + (max(layer.values()) + 1) * (NODE_W + GAP_X) - GAP_X,
        PAD * 2 + longest_chain * (CHAIN_W + CHAIN_GAP),
    )
    height = graph_bottom + 30 + len(chains) * (CHAIN_H + 50) + PAD
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#52606d"/>'
        "</marker></defs>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        _text(PAD, PAD + 8, title, size=16, weight="bold"),
    ]
    for (src, dst), (source, count) in sorted(table_edges.items()):
        (x1, y1), (x2, y2) = pos[src], pos[dst]
        x1, y1, y2 = x1 + NODE_W, y1 + NODE_H / 2, y2 + NODE_H / 2
        mid = (x1 + x2) / 2
        out.append(
            f'<path class="table-edge" d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}" '
            'fill="none" stroke="#52606d" stroke-width="1.4" marker-end="url(#arrow)"/>'
        )
        label = f"{count} column{'s' if count != 1 else ''}" + (f" ({source})" if source else "")
        out.append(_text(mid, (y1 + y2) / 2 - 5, label, size=10, anchor="middle", fill="#52606d"))
    for table, (x, y) in sorted(pos.items()):
        name, where = _short(table)
        out.append(
            f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="6" '
            'fill="#e4f0fb" stroke="#2b6cb0"/>'
        )
        out.append(_text(x + 10, y + 19, name, size=12, weight="bold"))
        out.append(_text(x + 10, y + 36, where, size=9, fill="#52606d"))
    y = graph_bottom + 10
    out.append(_text(PAD, y, "Column chains (every arrow is an API column edge)", 14, "bold"))
    for name, path in chains.items():
        y += 26
        out.append(_text(PAD, y, name, size=12, weight="bold"))
        top = y + 8
        for i, column in enumerate(path):
            x = PAD + i * (CHAIN_W + CHAIN_GAP)
            table, _ = _short(column.rsplit(".", 1)[0])
            out.append(
                f'<rect x="{x}" y="{top}" width="{CHAIN_W}" height="{CHAIN_H}" rx="5" '
                'fill="#fdf6e3" stroke="#b7791f"/>'
            )
            out.append(_text(x + 8, top + 16, table, size=9, fill="#52606d"))
            out.append(_text(x + 8, top + 32, column.rsplit(".", 1)[1], size=12, weight="bold"))
            if i:
                out.append(
                    f'<path class="column-edge" d="M{x - CHAIN_GAP},{top + CHAIN_H / 2} '
                    f'L{x},{top + CHAIN_H / 2}" stroke="#b7791f" stroke-width="1.6" '
                    'marker-end="url(#arrow)"/>'
                )
        y = top + CHAIN_H + 16
    out.append("</svg>")
    return "\n".join(out) + "\n"
