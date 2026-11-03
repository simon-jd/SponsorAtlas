"""Train, score and cluster the real input, then measure cluster-level P/R on agent labels.

Holdout endpoints are excluded from u/EM. Only the train split is reported unless
--evaluate-holdout is given; the holdout is meant to be scored once, after the design is
fixed on train. Labels are subagent labels, not human labels.
"""

import argparse
import csv
import json
import time
from collections import Counter

import pyarrow.parquet as pq
from splink import DuckDBAPI, Linker

from er.io import connect, dump, run_dir
from er.predict import constrained_clusters
from er.settings import CURVE, THRESHOLD
from er.train import load_input, train


def read_labels(path):
    csv.field_size_limit(16 * 1024 * 1024)  # the agent CSV embeds full source evidence
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["label"] = None if r["label"] == "" else int(float(r["label"]))
        r["hard_case"] = r["hard_case"] not in ("", "0", "0.0")
    return rows


def score(records_path, model_dir, work):
    c = connect(work / "score.duckdb")
    try:
        load_input(c, records_path)
        data = DuckDBAPI(c).register(c.table("input_records"), dataset_display_name="experiment")
        pred = Linker(data, str(model_dir / "model.json")).inference.predict(
            threshold_match_probability=0.0
        )
        return c.sql(
            f"SELECT unique_id_l, unique_id_r, match_probability FROM {pred.physical_name}"
        ).fetchall()
    finally:
        c.close()


def metrics(pairs, membership, decisions):
    direct = {(d["left_id"], d["right_id"]): d["reason"] for d in decisions}
    counts = Counter()
    misses = Counter()
    hard = Counter()
    for p in pairs:
        if p["label"] is None:
            continue
        same = membership[p["left_id"]] == membership[p["right_id"]]
        cell = ("TP" if same else "FN") if p["label"] else ("FP" if same else "TN")
        counts[cell] += 1
        if p["hard_case"]:
            hard[cell] += 1
        if cell in ("FN", "FP"):
            key = tuple(sorted((p["left_id"], p["right_id"])))
            misses[f"{cell}:{direct.get(key, 'NO_CANDIDATE')}"] += 1

    def pr(c):
        tp, fp, fn = c["TP"], c["FP"], c["FN"]
        return {
            **{k: c[k] for k in ("TP", "FP", "FN", "TN")},
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }

    return {
        **pr(counts),
        "abstained": sum(p["label"] is None for p in pairs),
        "hard_cases": pr(hard),
        "errors_by_direct_reason": dict(sorted(misses.items())),
    }


def run(input_dir, labels_path, output, evaluate_holdout=False):
    started = time.monotonic()
    inp = run_dir(input_dir)
    out = run_dir(output, create=True)
    labels = read_labels(labels_path)
    holdout_ids = {
        r[k] for r in labels if r["split"] == "holdout" for k in ("left_id", "right_id")
    }
    manifest = train(inp, out / "model", excluded_ids=holdout_ids)
    edges = score(inp / "records.parquet", out / "model", out)
    rows = pq.read_table(inp / "records.parquet").to_pylist()
    splits = ["train", "holdout"] if evaluate_holdout else ["train"]
    report = {
        "label_origin": "subagent",
        "human_labels": False,
        "threshold": THRESHOLD,
        "targets": {"precision": 0.97, "recall": 0.90},
        "excluded_holdout_records": manifest["excluded_holdout_records"],
        "training_records": manifest["records"],
        "candidates": len(edges),
        "splits": {},
    }
    for t in CURVE:
        membership, decisions = constrained_clusters(rows, edges, threshold=t)
        if t == THRESHOLD:
            report["decisions"] = dict(Counter(d["reason"] for d in decisions))
        for split in splits:
            m = metrics([p for p in labels if p["split"] == split], membership, decisions)
            entry = report["splits"].setdefault(split, {"curve": {}})
            entry["curve"][str(t)] = {k: m[k] for k in ("TP", "FP", "FN", "TN", "precision", "recall")}
            if t == THRESHOLD:
                entry["at_threshold"] = m
    for split, entry in report["splits"].items():
        at = entry["at_threshold"]
        entry["meets_targets"] = bool(
            at["precision"] is not None
            and at["recall"] is not None
            and at["precision"] >= 0.97
            and at["recall"] >= 0.90
        )
    report["seconds"] = round(time.monotonic() - started, 1)
    dump(out / "report.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--evaluate-holdout", action="store_true")
    a = p.parse_args()
    report = run(a.input, a.labels, a.output, a.evaluate_holdout)
    print(json.dumps({s: e["at_threshold"] for s, e in report["splits"].items()}, indent=1))


if __name__ == "__main__":
    main()
