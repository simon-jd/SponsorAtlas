"""Generate UNLABELED human work items. Never derives truth from rules/probability."""

import argparse
import csv
import hashlib
import json
from collections import defaultdict

import pyarrow.parquet as pq

from er.io import digest, dump, run_dir
from er.settings import SEED

FIELDS = [
    "left_id",
    "right_id",
    "left_source_system",
    "left_source_employer_key",
    "right_source_system",
    "right_source_employer_key",
    "label",
    "hard_case",
    "label_method",
    "reviewer",
    "reviewed_at",
    "evidence",
    "rationale",
    "split",
    "candidate_stratum",
]


def rank(value):
    return hashlib.sha256(f"{SEED}:{value}".encode()).hexdigest()


def assign_splits(pairs):
    """Freeze connected candidate components, preventing record leakage across splits."""
    parent = {i: i for r in pairs for i in (r["left_id"], r["right_id"])}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r in pairs:
        a, b = sorted((find(r["left_id"]), find(r["right_id"])))
        parent[b] = a
    for r in pairs:
        r["split"] = "holdout" if int(rank(find(r["left_id"]))[:8], 16) % 5 == 0 else "train"
    return pairs


def candidates(input_dir, prediction_dir, output, count=600):
    if count < 500 or count > 2000:
        raise ValueError("review batch size must be 500..2000")
    inp = run_dir(input_dir)
    pred = run_dir(prediction_dir)
    out = run_dir(output)
    if out.exists():
        raise ValueError("refusing to overwrite a review batch")
    input_hash = digest(inp / "records.parquet")
    provenance = json.loads((pred / "prediction.json").read_text())
    if provenance["input_records_sha256"] != input_hash:
        raise ValueError("prediction/input provenance mismatch")
    records = pq.read_table(inp / "records.parquet").to_pylist()
    by_id = {r["unique_id"]: r for r in records}
    decisions = pq.read_table(pred / "pair_decisions.parquet").to_pylist()
    strata = defaultdict(list)
    for d in decisions:
        strata[d["reason"]].append(tuple(sorted((d["left_id"], d["right_id"]))))
    # Outside-block sampling is necessary to expose missed candidates/recall, not just score precision.
    groups = defaultdict(list)
    for r in records:
        if r["eligible"]:
            groups[r["name_norm"]].append(r["unique_id"])
    for name, ids in sorted(groups.items()):
        if len(strata["SAME_NAME_DIFFERENT_STATE"]) >= 5000:
            break
        ids = sorted(ids, key=rank)[:12]
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                if by_id[left]["state"] != by_id[right]["state"]:
                    strata["SAME_NAME_DIFFERENT_STATE"].append(tuple(sorted((left, right))))
    ordered = sorted((r["unique_id"] for r in records if r["eligible"]), key=rank)
    for i in range(min(2000, len(ordered) // 2)):
        strata["RANDOM_SOURCE_PAIRS"].append(tuple(sorted((ordered[i], ordered[-i - 1]))))
    pools = {k: iter(sorted(set(v), key=rank)) for k, v in strata.items()}
    selected = []
    seen = set()
    while pools and len(selected) < count:
        for stratum in sorted(pools):
            try:
                pair = next(pools[stratum])
            except StopIteration:
                del pools[stratum]
                continue
            if pair in seen:
                continue
            seen.add(pair)
            left, right = pair
            l, r = by_id[left], by_id[right]
            item = {k: "" for k in FIELDS}
            item.update(
                left_id=left,
                right_id=right,
                left_source_system=l["source_system"],
                left_source_employer_key=l["source_employer_key"],
                right_source_system=r["source_system"],
                right_source_employer_key=r["source_employer_key"],
                candidate_stratum=stratum,
                evidence="SOURCE_POINTER_ONLY; input_records_sha256=" + input_hash,
            )
            selected.append(item)
            if len(selected) == count:
                break
    if len(selected) < count:
        raise ValueError("insufficient unique source candidate pairs")
    assign_splits(selected)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(selected)
    summary = {
        "status": "UNLABELED_NOT_ACCEPTANCE",
        "seed": SEED,
        "pairs": len(selected),
        "split_policy": "candidate-connected-components-hash-seed42-20pct-holdout",
        "input_records_sha256": digest(inp / "records.parquet"),
        "batch_sha256": digest(out),
        "strata": {k: sum(r["candidate_stratum"] == k for r in selected) for k in strata},
        "holdout": sum(r["split"] == "holdout" for r in selected),
    }
    dump(out.with_suffix(".json"), summary)
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--count", type=int, default=600)
    a = p.parse_args()
    print(json.dumps(candidates(a.input, a.predictions, a.output, a.count)))


if __name__ == "__main__":
    main()
