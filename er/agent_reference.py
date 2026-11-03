"""Audit frozen subagent references at one threshold; never human acceptance or training.

Trust roots are caller-supplied SHA256 values for the frozen annotation manifest
and research FINAL-BINDING. They must come from an independently reviewed receipt,
not be regenerated from untrusted replacement files. No registry is published.
"""

import argparse
import csv
import json
import os
import re
from pathlib import Path

import pyarrow.parquet as pq

from er.agent_reference_labels import (
    RECORD_SCHEMA,
    decode_json,
    require,
    validate_references,
    verify_revisions,
    verify_source_evidence,
)
from er.evaluate import evaluate_pairs, pair_key
from er.io import ROOT, TOOLS, digest, run_dir
from er.lineage import validate_input
from er.predict import constrained_clusters
from er.registry import rebuild_registry
from er.settings import MAX_RECORDS, SEED, THRESHOLD

MAX_BYTES = 128 * 1024 * 1024
MAX_LABEL_BYTES = 16 * 1024 * 1024
MAX_SCORES = 500_000
SOURCE_CODE = (
    "er/train.py",
    "er/predict.py",
    "er/io.py",
    "er/settings.py",
    "er/lineage.py",
    "er/registry.py",
    "scripts/local_duckdb.py",
)


def read_json(path):
    return decode_json(Path(path).read_text())


def read_csv(path):
    try:
        with path.open(newline="") as file:
            return list(csv.DictReader(file))
    except csv.Error as exc:
        raise ValueError(f"invalid CSV evidence ({path.name}): {exc}") from exc


def file_path(path):
    path = run_dir(path)
    require(path.is_file() and path.suffix != ".duckdb", "bounded artifact file required")
    require(path.stat().st_size <= MAX_BYTES, f"artifact size limit: {path.name}")
    return path


def output_path(output, sources):
    raw = Path(os.path.abspath(output))
    require(not any(p.is_symlink() for p in (raw, *raw.parents)), "output symlink alias forbidden")
    require(
        raw.resolve().is_relative_to(TOOLS.resolve()), "output must remain inside project .tools"
    )
    out = run_dir(raw)
    require(out != TOOLS.resolve() and not out.exists(), "output must be new immutable namespace")
    for source in sources:
        source = Path(source).resolve()
        require(
            not out.is_relative_to(source) and not source.is_relative_to(out),
            "output overlaps a source namespace",
        )
    return out


def bind(path, expected, observed):
    path = file_path(path)
    require(
        type(expected) is str and re.fullmatch("[a-f0-9]{64}", expected),
        f"missing trusted digest: {path.name}",
    )
    actual = digest(path)
    require(actual == expected, f"artifact digest mismatch: {path.name}")
    prior = observed.setdefault(path, actual)
    require(prior == actual, f"artifact changed during binding: {path.name}")
    return actual


def read_parquet(path, limit, columns=None, expected_schema=None):
    file = pq.ParquetFile(path)
    require(file.metadata.num_rows <= limit, f"bounded Parquet row limit: {path.name}")
    if expected_schema is not None:
        actual_schema = {field.name: str(field.type) for field in file.schema_arrow}
        require(
            all(actual_schema.get(key) == kind for key, kind in expected_schema.items())
            and (columns is not None or len(file.schema_arrow) == len(expected_schema)),
            f"Parquet schema/type mismatch: {path.name}",
        )
    return file.read(columns=columns, use_threads=False).to_pylist()


def metrics(rows, canonical, direct_pairs):
    truth = [{**r, "label": str(r["label"])} for r in rows]
    decisions = {
        pair_key(r["left_id"], r["right_id"]): float(
            canonical[r["left_id"]] is not None
            and canonical[r["left_id"]] == canonical[r["right_id"]]
        )
        for r in rows
    }
    result = evaluate_pairs(truth, decisions, 0.5)
    result["direct_score_missing"] = sum(
        pair_key(r["left_id"], r["right_id"]) not in direct_pairs for r in rows
    )
    return result, decisions


def evaluate(
    *,
    input_dir,
    model_dir,
    prediction_dir,
    labels_path,
    candidates_path,
    frozen_candidates_path,
    label_manifest_path,
    label_manifest_sha256,
    input_binding_path,
    research_binding_path,
    research_binding_sha256,
    output,
    previous_registry=None,
):
    inp, model, pred = [run_dir(p) for p in (input_dir, model_dir, prediction_dir)]
    labels, candidates_path, frozen_path, label_manifest, input_binding, research_binding = [
        file_path(p)
        for p in (
            labels_path,
            candidates_path,
            frozen_candidates_path,
            label_manifest_path,
            input_binding_path,
            research_binding_path,
        )
    ]
    research = research_binding.parent
    sources = [
        inp,
        model,
        pred,
        research,
        labels.parent,
        candidates_path,
        frozen_path,
        input_binding,
        research_binding,
    ]
    if previous_registry is not None:
        previous_registry = file_path(previous_registry)
        sources.append(previous_registry)
    out = output_path(output, sources)
    observed = {}
    bind(label_manifest, label_manifest_sha256, observed)
    bind(research_binding, research_binding_sha256, observed)
    manifest, final = read_json(label_manifest), read_json(research_binding)
    require(
        manifest.get("status") == "complete"
        and type(manifest.get("version")) is int
        and manifest["version"] == 2
        and manifest.get("label_origin") == "subagent",
        "invalid frozen subagent label manifest",
    )
    bind(labels, manifest.get("agent_pairs_sha256"), observed)
    require(labels.stat().st_size <= MAX_LABEL_BYTES, "label file size limit")
    outputs_path = label_manifest.parent / "output-hashes.json"
    bind(outputs_path, manifest.get("output_hash_manifest_sha256"), observed)
    output_hashes = read_json(outputs_path)
    # Fixed names only: never follow arbitrary paths from an untrusted manifest.
    for path in (labels, input_binding, label_manifest.parent / "validation.json"):
        require(path.parent == label_manifest.parent, "label binding must share frozen namespace")
        bind(path, output_hashes.get(path.name), observed)
    # Verify source-assisted revision provenance bytes, without fetching any URL.
    for name in (
        "external-evidence-input-snapshot.jsonl",
        "independently-reviewed-primary-sources.jsonl",
        "revision-audit.jsonl",
        "input-quality-findings.jsonl",
    ):
        if name in output_hashes:
            bind(label_manifest.parent / name, output_hashes[name], observed)
    validation = read_json(label_manifest.parent / "validation.json")
    input_hashes = read_json(input_binding)
    for path in (candidates_path, inp / "records.parquet", inp / "occurrences.parquet"):
        bind(path, input_hashes.get(str(path.relative_to(ROOT))), observed)
    final_hashes = final.get("all_artifacts_sha256", {})
    artifact_path = research / "artifact-binding.json"
    bind(artifact_path, final_hashes.get("artifact-binding.json"), observed)
    artifact_hashes = read_json(artifact_path).get("artifacts", {})
    required = [
        model / "training.json",
        model / "model.json",
        model / "training_ids.txt",
        pred / "prediction.json",
        pred / "scores.parquet",
        pred / "int_employer_xref.parquet",
        pred / "registry.json",
        research / "source-binding.json",
        research / "driver.py",
        research / "holdout-endpoint-ids.txt",
        frozen_path,
    ]
    for path in required:
        require(path.is_relative_to(research), "research artifact outside frozen binding namespace")
        key = str(path.relative_to(research))
        bind(path, final_hashes.get(key), observed)
        require(
            artifact_hashes.get(key) == final_hashes.get(key),
            f"cross-manifest artifact digest mismatch: {path.name}",
        )
    source_binding = read_json(research / "source-binding.json")
    for path in (
        inp / "input.json",
        inp / "records.parquet",
        inp / "occurrences.parquet",
        candidates_path,
    ):
        bind(path, source_binding.get("files", {}).get(str(path.relative_to(ROOT))), observed)
    # Bind the implementation that originally made the artifacts; code changes require review.
    for name in SOURCE_CODE:
        path = ROOT / name
        require(
            digest(path) == source_binding.get("files", {}).get(name),
            f"research source implementation digest mismatch: {name}",
        )
        observed[path] = source_binding["files"][name]
    training, prediction = read_json(model / "training.json"), read_json(pred / "prediction.json")
    require(
        training.get("driver_sha256")
        == prediction.get("driver_sha256")
        == source_binding.get("driver_sha256")
        == observed[research / "driver.py"],
        "research driver provenance digest mismatch",
    )
    research_commit = source_binding.get("git_head")
    require(
        type(research_commit) is str
        and re.fullmatch("[a-f0-9]{40}", research_commit)
        and training.get("git_head")
        == prediction.get("git_head")
        == final.get("git_head")
        == research_commit,
        "research source commit provenance mismatch",
    )
    for metadata in (training, prediction):
        require(
            all(
                metadata.get(k) is v
                for k, v in {
                    "acceptance_eligible": False,
                    "human_acceptance_eligible": False,
                    "research_only": True,
                    "agent_eval": True,
                    "threshold_tuning": False,
                }.items()
            )
            and metadata.get("label_origin") == "subagent",
            "research subagent classification must remain acceptance-ineligible",
        )
        require(
            type(metadata.get("threshold")) is float and metadata["threshold"] == THRESHOLD,
            f"fixed threshold {THRESHOLD} required",
        )
    require(type(training.get("seed")) is int and training["seed"] == SEED, "fixed seed42 required")
    require(
        prediction.get("training_manifest_sha256") == observed[model / "training.json"],
        "prediction/training manifest digest mismatch",
    )
    for path, expected in (
        (model / "model.json", training.get("model_sha256")),
        (model / "training_ids.txt", training.get("training_ids_sha256")),
        (pred / "scores.parquet", prediction.get("scores_sha256")),
        (pred / "int_employer_xref.parquet", prediction.get("xref_sha256")),
        (pred / "registry.json", prediction.get("registry_sha256")),
    ):
        require(observed[path] == expected, f"cross-manifest digest mismatch: {path.name}")
    require(
        prediction.get("model_sha256") == training.get("model_sha256"),
        "prediction/model digest mismatch",
    )
    frozen_hash = observed[frozen_path]
    require(
        training.get("frozen_pairs_sha256")
        == source_binding.get("frozen_pairs_sha256")
        == frozen_hash,
        "frozen candidate digest lineage mismatch",
    )
    input_metadata = validate_input(inp, training=training, prediction=prediction)
    require(
        training.get("input_kind") == prediction.get("input_kind") == input_metadata.get("kind"),
        "input kind lineage mismatch",
    )
    require(
        input_metadata.get("kind") in ("real-staging-lca-uscis", "synthetic-test-only"),
        "unsupported input provenance kind",
    )
    candidates = read_csv(candidates_path)
    frozen = read_csv(frozen_path)
    # Reuse strict JSON decoder for each bounded label row.
    rows = [decode_json(line) for line in labels.read_text().splitlines()]
    counts = validate_references(rows, candidates, frozen, manifest, validation)
    verify_revisions(rows, manifest, validation, label_manifest.parent, observed)
    all_holdout = [r for r in rows if r["split"] == "holdout"]
    endpoints = {r[side + "_id"] for r in all_holdout for side in ("left", "right")}
    frozen_ids_path = research / "holdout-endpoint-ids.txt"
    frozen_ids = frozen_ids_path.read_text().splitlines()
    require(
        len(frozen_ids) == len(set(frozen_ids)) and set(frozen_ids) == endpoints,
        "all frozen holdout endpoint IDs mismatch (including abstentions)",
    )
    require(
        training.get("excluded_ids_sha256")
        == source_binding.get("excluded_ids_sha256")
        == observed[frozen_ids_path],
        "holdout endpoint exclusion digest mismatch",
    )
    training_list = (model / "training_ids.txt").read_text().splitlines()
    training_ids = set(training_list)
    require(
        training_ids
        and len(training_ids) == len(training_list)
        and type(training.get("records")) is int
        and training["records"] == len(training_ids),
        "actual training IDs count/uniqueness mismatch",
    )
    require(not endpoints & training_ids, "all holdout endpoints must be excluded from training")
    records = read_parquet(inp / "records.parquet", MAX_RECORDS, expected_schema=RECORD_SCHEMA)
    require(
        len(records) == input_metadata.get("source_records") == prediction.get("source_records"),
        "input record count/coverage mismatch",
    )
    by_id = verify_source_evidence(rows, records, inp / "occurrences.parquet", input_metadata)
    require(
        training_ids <= set(by_id) and all(by_id[uid]["eligible"] for uid in training_ids),
        "training IDs absent from actual eligible input",
    )
    scores = read_parquet(
        pred / "scores.parquet",
        MAX_SCORES,
        ["left_id", "right_id", "match_probability"],
        expected_schema={"left_id": "string", "right_id": "string", "match_probability": "double"},
    )
    require(len(scores) == prediction.get("candidates"), "scores count mismatch")
    edges = [(r["left_id"], r["right_id"], r["match_probability"]) for r in scores]
    del scores
    direct_pairs = {pair_key(left, right) for left, right, _ in edges}
    require(len(direct_pairs) == len(edges), "duplicate scored canonical pair")
    membership, decision_rows = constrained_clusters(records, edges, THRESHOLD)
    del decision_rows, edges
    # Existing exact parent-registry contract, never a new initialization to bypass history.
    mode = prediction.get("registry_mode")
    expected_parent = prediction.get("registry_previous_sha256")
    recorded_parent = prediction.get("registry_previous_path")
    require(type(mode) is str and mode in ("INITIALIZE", "INHERIT"), "invalid registry parent mode")
    require(
        "registry_previous_sha256" in prediction and "registry_previous_path" in prediction,
        "explicit registry parent metadata required",
    )
    if mode == "INITIALIZE":
        require(
            expected_parent is None and recorded_parent is None and previous_registry is None,
            "INITIALIZE registry parent path/digest/override must be absent",
        )
    else:
        require(
            type(expected_parent) is str and re.fullmatch("[a-f0-9]{64}", expected_parent),
            "INHERIT registry parent requires exact SHA256",
        )
        require(
            type(recorded_parent) is str and bool(recorded_parent.strip()),
            "INHERIT registry parent recorded path required",
        )
        # A relocation override selects the only parent path the shared helper may read.
        parent = previous_registry if previous_registry is not None else recorded_parent
        parent = file_path(parent)
        output_path(out, sources + [parent])
        bind(parent, expected_parent, observed)
        previous_registry = parent
    canonical, rebuilt, published = rebuild_registry(
        records, membership, input_metadata, prediction, pred, previous_registry
    )
    require(
        rebuilt == published, "published registry disagrees with scored registry provenance rebuild"
    )
    del membership, rebuilt, published
    xref = read_parquet(
        pred / "int_employer_xref.parquet",
        MAX_RECORDS,
        ["unique_id", "employer_id", "source_system", "source_employer_key"],
        expected_schema={
            key: "string"
            for key in ("unique_id", "employer_id", "source_system", "source_employer_key")
        },
    )
    deployed = {r["unique_id"]: r for r in xref}
    require(
        len(deployed) == len(xref) and set(deployed) == set(by_id),
        "deployed xref missing/duplicate input records",
    )
    require(
        all(
            r["employer_id"] == canonical[uid]
            and r["source_system"] == by_id[uid]["source_system"]
            and r["source_employer_key"] == by_id[uid]["source_employer_key"]
            for uid, r in deployed.items()
        ),
        "xref disagrees with scored canonical registry/source",
    )
    holdout = [r for r in all_holdout if r["label"] is not None]
    chosen, _ = metrics(holdout, canonical, direct_pairs)
    hard = [r for r in holdout if r["hard_case"] == 1]
    chosen["hard_cases"], _ = metrics(hard, canonical, direct_pairs)
    chosen["threshold"] = THRESHOLD
    train = [r for r in rows if r["split"] == "train" and r["label"] is not None]
    train_metrics, train_decisions = metrics(train, canonical, direct_pairs)
    train_errors = [
        {
            "left_id": r["left_id"],
            "right_id": r["right_id"],
            "truth": r["label"],
            "predicted": int(train_decisions[pair_key(r["left_id"], r["right_id"])]),
            "hard_case": r["hard_case"],
            "rationale": r["rationale"][:1000],
        }
        for r in train
        if bool(train_decisions[pair_key(r["left_id"], r["right_id"])]) != bool(r["label"])
    ]
    passed = chosen["precision"] >= 0.97 and chosen["recall"] >= 0.90
    result = {
        "status": "AGENT_REFERENCE_THRESHOLDS_MET"
        if passed
        else "AGENT_REFERENCE_THRESHOLDS_NOT_MET",
        "evidence_origin": "subagent",
        "label_method": "subagent",
        "human_evidence": False,
        "human_acceptance_eligible": False,
        "acceptance_eligible": False,
        "research_only": True,
        "input_kind": input_metadata["kind"],
        "threshold": THRESHOLD,
        "seed": SEED,
        "targets": {"precision": 0.97, "recall": 0.90},
        "counts": counts,
        "holdout": {
            "candidates": len(all_holdout),
            "resolved": len(holdout),
            "abstained": len(all_holdout) - len(holdout),
            "all_endpoints": len(endpoints),
            "training_intersection": len(endpoints & training_ids),
        },
        "metrics": chosen,
        "development": {
            "split": "train",
            "development_only": True,
            "no_tuning_or_retraining_performed": True,
            "resolved": len(train),
            "abstained": sum(r["split"] == "train" and r["label"] is None for r in rows),
            "metrics": train_metrics,
            "error_count": len(train_errors),
            "errors": train_errors[:100],
            "errors_truncated": len(train_errors) > 100,
        },
        "registry_context": {
            "mode": prediction["registry_mode"],
            "previous_sha256": prediction["registry_previous_sha256"],
            "production_registry_replaced": False,
            "research_git_head": research_commit,
        },
        "caveats": [
            "Source-assisted subagent judgments are weak references, not human or legal truth.",
            "Abstentions remain unknown and excluded from resolved-only metric denominators.",
            f"Already-seen holdout used only at frozen threshold {THRESHOLD}; no curve or per-pair holdout errors exported.",
            "Source placeholders, incomplete tax IDs and missing context may limit entity judgments.",
            "Local evidence does not close Databricks, Snowflake or whole-project acceptance.",
        ],
        "source_releases": input_metadata["originals"],
    }
    result["source_quality"] = {
        "input_issue_counts": input_metadata.get("issues", {}),
        "caveat": "Source issues and frozen reviewer findings are limitations, not legal verification.",
        "findings_sha256": observed.get(label_manifest.parent / "input-quality-findings.jsonl"),
    }
    result["source_record_hash_contract"] = {
        "algorithm": "sha256",
        "sort_keys": True,
        "ensure_ascii": True,
        "separators": [",", ":"],
        "actual_full_record_schema": RECORD_SCHEMA,
        "label_endpoint_occurrences_verified": len(rows) * 2,
        "original_context_generator_available": False,
        "basis": "Frozen snapshot serialization empirically corroborated; all source hashes independently recomputed from actual full input records.",
    }
    # Recheck bytes before committing the immutable report; only explicit bound files are read.
    for path, expected in observed.items():
        require(digest(path) == expected, f"artifact changed during evaluation: {path.name}")
    result["artifacts_sha256"] = {
        str(path.relative_to(ROOT)): value for path, value in observed.items()
    }
    result["consumer_sources_sha256"] = {
        "er/agent_reference.py": digest(Path(__file__)),
        "er/agent_reference_labels.py": digest(
            Path(__file__).with_name("agent_reference_labels.py")
        ),
    }
    output_path(out, sources)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(mode=0o700, exist_ok=False)
    descriptor = os.open(out / "report.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in (
        "input",
        "model",
        "predictions",
        "labels",
        "candidates",
        "frozen-candidates",
        "label-manifest",
        "label-manifest-sha256",
        "input-binding",
        "research-binding",
        "research-binding-sha256",
        "output",
    ):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--previous-registry", help="exact frozen parent registry, never a reset")
    args = vars(parser.parse_args(argv))
    for source, target in (
        ("input", "input_dir"),
        ("model", "model_dir"),
        ("predictions", "prediction_dir"),
        ("labels", "labels_path"),
        ("candidates", "candidates_path"),
        ("frozen_candidates", "frozen_candidates_path"),
        ("label_manifest", "label_manifest_path"),
        ("input_binding", "input_binding_path"),
        ("research_binding", "research_binding_path"),
    ):
        args[target] = args.pop(source)
    try:
        result = evaluate(**args)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(
            json.dumps(
                {
                    "status": "INVALID_AGENT_REFERENCE_EVIDENCE",
                    "evidence_origin": "subagent",
                    "human_evidence": False,
                    "human_acceptance_eligible": False,
                    "error": str(exc),
                }
            )
        )
        raise SystemExit(2) from exc
    # Do not print even train diagnostics to a public log accidentally.
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "evidence_origin",
                    "human_evidence",
                    "human_acceptance_eligible",
                    "counts",
                    "holdout",
                    "metrics",
                )
            }
        )
    )
    raise SystemExit(0 if result["status"] == "AGENT_REFERENCE_THRESHOLDS_MET" else 1)


if __name__ == "__main__":
    main()
