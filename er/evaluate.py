"""Strict human holdout gate; synthetic regressions cannot produce acceptance."""

import argparse
import csv
import json
import math
from datetime import datetime

import pyarrow.parquet as pq

from er.io import digest, dump, run_dir
from er.lineage import validate_input
from er.predict import constrained_clusters
from er.registry import IdentityReviewRequired, rebuild_registry
from er.settings import CURVE, THRESHOLD


def pair_key(left, right):
    return tuple(sorted((left, right)))


def validate_labels(rows):
    if len(rows) < 500:
        raise ValueError("requires at least 500 human-labeled employer pairs (100 hard cases)")
    if any(r.get("label_method") != "human" for r in rows):
        raise ValueError("human label_method required; rule/synthetic truth forbidden")
    seen = set()
    splits = {}
    hard = 0
    for r in rows:
        if r.get("label") not in {"0", "1"} or r.get("hard_case") not in {"0", "1"}:
            raise ValueError("all labels and hard_case must be binary and reviewed")
        for k in (
            "left_id",
            "right_id",
            "reviewer",
            "reviewed_at",
            "evidence",
            "rationale",
            "left_source_system",
            "left_source_employer_key",
            "right_source_system",
            "right_source_employer_key",
        ):
            if not r.get(k, "").strip():
                raise ValueError(f"missing audit metadata: {k}")
        stamp = datetime.fromisoformat(r["reviewed_at"])
        if stamp.tzinfo is None:
            raise ValueError("reviewed_at requires a timezone")
        if r.get("split") not in {"train", "holdout"}:
            raise ValueError("explicit frozen train/holdout split required")
        pair = pair_key(r["left_id"], r["right_id"])
        if pair[0] == pair[1] or pair in seen:
            raise ValueError("duplicate/self label pair")
        seen.add(pair)
        hard += int(r["hard_case"])
        for uid in pair:
            if uid in splits and splits[uid] != r["split"]:
                raise ValueError("record leakage between train and holdout")
            splits[uid] = r["split"]
    if hard < 100:
        raise ValueError("requires at least 100 human hard cases")
    holdout = [r for r in rows if r["split"] == "holdout"]
    if {r["label"] for r in holdout} != {"0", "1"} or not any(
        r["hard_case"] == "1" for r in holdout
    ):
        raise ValueError("holdout needs positive, negative and hard-case labels")
    if not any(r["split"] == "train" for r in rows):
        raise ValueError("separate training/development labels required")
    return holdout


def evaluate_pairs(labels, scores, threshold=THRESHOLD):
    if not labels:
        raise ValueError("empty evaluation")
    tp = fp = fn = tn = missing = 0
    for r in labels:
        key = pair_key(r["left_id"], r["right_id"])
        score = scores.get(key)
        if score is not None and (not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("invalid probability")
        if score is None:
            missing += 1
        predicted = score is not None and score >= threshold
        truth = r["label"] == "1"
        tp += predicted and truth
        fp += predicted and not truth
        fn += not predicted and truth
        tn += not predicted and not truth
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "missing_predictions": missing,
        "pairs": len(labels),
    }


def evaluate(input_dir, model_dir, prediction_dir, labels_path, output, previous_registry=None):
    inp = run_dir(input_dir)
    model = run_dir(model_dir)
    pred = run_dir(prediction_dir)
    out = run_dir(output)
    trained = json.loads((model / "training.json").read_text())
    prediction = json.loads((pred / "prediction.json").read_text())
    input_metadata = validate_input(inp, training=trained, prediction=prediction)
    with open(labels_path, newline="") as f:
        labels = list(csv.DictReader(f))
    holdout = validate_labels(
        labels
    )  # Fail before touching predictions when human evidence is absent.
    if trained.get("input_kind") != "real-staging-lca-uscis":
        raise ValueError("human acceptance requires real employer staging, not a synthetic fixture")
    if trained.get("labels_sha256") != digest(labels_path) or not trained.get(
        "acceptance_eligible"
    ):
        raise ValueError("retrain after freezing reviewed labels; development model cannot pass")
    for path, expected in [
        (model / "training_ids.txt", trained["training_ids_sha256"]),
        (model / "model.json", trained["model_sha256"]),
        (inp / "records.parquet", trained["input_records_sha256"]),
        (pred / "scores.parquet", prediction["scores_sha256"]),
        (pred / "int_employer_xref.parquet", prediction["xref_sha256"]),
    ]:
        if digest(path) != expected:
            raise ValueError(f"artifact digest mismatch: {path.name}")
    if (
        prediction["model_sha256"] != trained["model_sha256"]
        or prediction["input_records_sha256"] != trained["input_records_sha256"]
    ):
        raise ValueError("prediction provenance does not match evaluation model/input")
    train_ids = set((model / "training_ids.txt").read_text().splitlines())
    if any(r[k] in train_ids for r in holdout for k in ("left_id", "right_id")):
        raise ValueError("holdout records leaked into EM/u training")
    records = pq.read_table(inp / "records.parquet").to_pylist()
    by_id = {r["unique_id"]: r for r in records}
    deployed = pq.read_table(pred / "int_employer_xref.parquet").to_pylist()
    deployed_by_id = {r["unique_id"]: r for r in deployed}
    if len(deployed_by_id) != len(deployed) or set(deployed_by_id) != set(by_id):
        raise ValueError("deployed xref is missing or duplicates input records")
    for r in labels:
        for side in ("left", "right"):
            row = by_id.get(r[f"{side}_id"])
            if row is None:
                raise ValueError("label endpoint absent from input")
            if (
                row["source_system"] != r[f"{side}_source_system"]
                or row["source_employer_key"] != r[f"{side}_source_employer_key"]
            ):
                raise ValueError("label source-key provenance mismatch")
    scores = pq.read_table(pred / "scores.parquet").to_pylist()
    edges = [(r["left_id"], r["right_id"], r["match_probability"]) for r in scores]
    curve = []
    errors = []
    direct_pairs = {pair_key(left, right) for left, right, _ in edges}
    for threshold in CURVE:
        members, _ = constrained_clusters(records, edges, threshold)
        try:
            canonical, rebuilt, published = rebuild_registry(
                records, members, input_metadata, prediction, pred, previous_registry
            )
        except IdentityReviewRequired as exc:
            if threshold == THRESHOLD:
                raise
            curve.append({"threshold": threshold, "status": exc.status, "error": str(exc)})
            continue
        if threshold == THRESHOLD:
            if rebuilt != published:
                raise ValueError("deployed registry disagrees with registry provenance rebuild")
            if any(deployed_by_id[uid]["employer_id"] != canonical[uid] for uid in by_id):
                raise ValueError("deployed xref disagrees with scored canonical registry")
        # Measure deployed constrained clusters (including transitivity and deterministic IDs).
        decisions = {
            pair_key(r["left_id"], r["right_id"]): float(
                canonical[r["left_id"]] is not None
                and canonical[r["left_id"]] == canonical[r["right_id"]]
            )
            for r in holdout
        }
        m = evaluate_pairs(holdout, decisions, 0.5)
        m["direct_score_missing"] = sum(
            pair_key(r["left_id"], r["right_id"]) not in direct_pairs for r in holdout
        )
        hard = [r for r in holdout if r["hard_case"] == "1"]
        m["hard_cases"] = evaluate_pairs(hard, decisions, 0.5)
        m["threshold"] = threshold
        curve.append(m)
        if threshold == THRESHOLD:
            for r in holdout:
                actual = bool(decisions[pair_key(r["left_id"], r["right_id"])])
                if actual != (r["label"] == "1"):
                    errors.append(
                        {
                            "left_id": r["left_id"],
                            "right_id": r["right_id"],
                            "truth": r["label"],
                            "predicted": int(actual),
                            "hard_case": r["hard_case"],
                            "rationale": r["rationale"],
                        }
                    )
    chosen = next(m for m in curve if m["threshold"] == THRESHOLD)
    passed = chosen["precision"] >= 0.97 and chosen["recall"] >= 0.90
    result = {
        "status": "PASS" if passed else "FAIL",
        "threshold": THRESHOLD,
        "labels": len(labels),
        "hard_cases": sum(r["hard_case"] == "1" for r in labels),
        "holdout_pairs": len(holdout),
        "metrics": chosen,
        "threshold_curve": curve,
        "errors": errors,
        "labels_sha256": digest(labels_path),
        "model_sha256": trained["model_sha256"],
        "input_occurrences_sha256": input_metadata["occurrences_sha256"],
        "registry_sha256": prediction["registry_sha256"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    dump(out, result)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--labels", default="er/labels/pairs.csv")
    p.add_argument("--output", required=True)
    p.add_argument(
        "--previous-registry", help="relocated parent registry; exact recorded hash required"
    )
    a = p.parse_args()
    try:
        result = evaluate(a.input, a.model, a.predictions, a.labels, a.output, a.previous_registry)
    except (ValueError, FileNotFoundError) as e:
        failure = {"status": "BLOCKED", "error": str(e)}
        out = run_dir(a.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        dump(out, failure)
        print(json.dumps(failure))
        raise SystemExit(2)
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
