"""Cluster-level P/R at several thresholds from one prediction's Splink scores.

Reads records.parquet of the ER input and scores.parquet of a prediction, rebuilds the
constrained clusters at each threshold (nothing is retrained or re-scored) and counts
TP/FP/FN/TN per label split. Labels are the frozen subagent reference, not human labels.
Pairs whose endpoints are not in the input are reported by er.label_alignment and left
out of the counts.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from er.io import dump, run_dir
from er.label_alignment import align
from er.predict import constrained_clusters
from er.recall_experiment import metrics, read_labels
from er.settings import THRESHOLD

THRESHOLDS = (0.85, THRESHOLD, 0.9)
RECORD_COLUMNS = [
    "unique_id", "source_system", "source_employer_key", "name_norm", "state", "city",
    "postcode", "strong_id", "tax_last4", "country", "eligible",
]  # fmt: skip


def partition_changes(before, after):
    """Records whose cluster (as a member set) differs; clusters only in before / after."""
    groups_before, groups_after = defaultdict(set), defaultdict(set)
    for uid, root in before.items():
        groups_before[root].add(uid)
    for uid, root in after.items():
        groups_after[root].add(uid)
    moved = {
        uid
        for uid in before
        if groups_before[before[uid]] != groups_after.get(after.get(uid), set())
    }
    old = {frozenset(g) for g in groups_before.values()}
    new = {frozenset(g) for g in groups_after.values()}
    return {
        "records_moved": len(moved),
        "clusters_removed": len(old - new),
        "clusters_added": len(new - old),
    }


def compare(records, edges, labels, thresholds=THRESHOLDS):
    alignment = align(labels, records)
    skipped = {u["index"] for u in alignment["unmatched"]}
    usable = [p for i, p in enumerate(labels) if i not in skipped]
    eligible = {r["unique_id"] for r in records if r["eligible"]}
    splits = sorted({p["split"] for p in usable})
    result = {"label_alignment": alignment, "thresholds": {}, "changes_vs_deployed": {}}
    members, accepted = {}, {}
    for t in thresholds:
        membership, decisions = constrained_clusters(records, edges, threshold=t)
        members[t] = membership
        entry = {
            "accepted_edges": sum(d["accepted"] for d in decisions),
            "clusters": len(set(membership.values())),
            "employers": len({membership[u] for u in eligible}),
        }
        for split in splits:
            entry[split] = metrics(
                [p for p in usable if p["split"] == split], membership, decisions
            )
        result["thresholds"][str(t)] = entry
        accepted[t] = {(d["left_id"], d["right_id"]) for d in decisions if d["accepted"]}
    if THRESHOLD in thresholds:
        for t in thresholds:
            if t == THRESHOLD:
                continue
            a, b = accepted[t], accepted[THRESHOLD]
            result["changes_vs_deployed"][str(t)] = {
                "accepted_only_here": len(a - b),
                "accepted_only_at_deployed": len(b - a),
                **partition_changes(members[t], members[THRESHOLD]),
            }
    return result


def run(input_dir, prediction_dir, labels_path, output):
    inp, pred = run_dir(input_dir), run_dir(prediction_dir)
    records = pq.read_table(inp / "records.parquet", columns=RECORD_COLUMNS).to_pylist()
    scores = pq.read_table(pred / "scores.parquet").to_pylist()
    edges = [(s["left_id"], s["right_id"], s["match_probability"]) for s in scores]
    report = {
        "label_origin": "subagent",
        "human_labels": False,
        "deployed_threshold": THRESHOLD,
        "input": str(Path(inp)),
        "prediction": str(Path(pred)),
        "labels": str(Path(labels_path).resolve()),
        "source_records": len(records),
        "candidates": len(edges),
        **compare(records, edges, read_labels(labels_path)),
    }
    dump(output, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--prediction", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    report = run(a.input, a.prediction, a.labels, a.output)
    summary = {
        t: {k: e[k] for k in ("accepted_edges", "clusters", "employers")}
        | {s: {m: e[s][m] for m in ("TP", "FP", "FN", "TN", "precision", "recall")}
           for s in ("train", "holdout") if s in e}
        for t, e in report["thresholds"].items()
    }  # fmt: skip
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
