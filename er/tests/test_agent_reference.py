"""Explicitly synthetic contract regressions, never employer quality evidence."""

import copy
import csv
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from er.agent_reference import SOURCE_CODE, evaluate, main
from er.settings import THRESHOLD
from er.agent_reference_labels import (
    RECORD_SCHEMA,
    SOURCE_FIELDS,
    annotation_record_hash,
    label_counts,
    validate_references,
    verify_source_evidence,
)
from er.io import ROOT, TOOLS, digest, dump
from er.normalize import prepare_record
from er.predict import constrained_clusters
from er.registry import content_hash, registry_metadata, resolve_registry, save_registry


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def frozen():
    """600 wholly invented pairs and a non-trained dummy model, all under .tools."""
    with tempfile.TemporaryDirectory(prefix="agent-reference-synthetic-", dir=TOOLS / "er") as temp:
        root = Path(temp) / "research"
        root.mkdir()
        inp, model, pred, annotation = [
            root / n for n in ("input", "model", "prediction", "labels")
        ]
        for directory in (inp, model, pred, annotation):
            directory.mkdir()
        records, occurrences, rows, candidates, edges = [], [], [], [], []
        for i in range(600):
            holdout = i < 108
            resolved = i < 95 if holdout else i < 516
            positive = i < 48 if holdout else 108 <= i < 266
            hard = i < 39 if holdout else 108 <= i < 234
            label = int(positive) if resolved else None
            side_evidence = {}
            source_hashes = {}
            for side, uid in [("left", f"L{i:04d}"), ("right", f"R{i:04d}")]:
                record = {
                    "unique_id": uid,
                    "source_system": "uscis",
                    "source_employer_key": uid,
                    "source_name": "SYNTHETIC " + uid,
                    "state": "CA",
                    "country": "US",
                    "city": "invented",
                    "postcode": "11111",
                    "tax_id": None,
                    "strong_id": None,
                    "tax_last4": None,
                    "issues": "",
                    "eligible": True,
                }
                record = prepare_record(record)
                records.append(record)
                occurrence = {
                    k: record[k]
                    for k in (
                        "unique_id",
                        "source_system",
                        "source_employer_key",
                        "source_name",
                        "state",
                        "country",
                        "city",
                        "postcode",
                        "tax_id",
                    )
                }
                occurrence.update(
                    source_release="SYNTHETIC",
                    source_sha256="a" * 64,
                    source_row_number=len(records),
                    source_url="https://www.uscis.gov/synthetic-only.csv",
                )
                occurrences.append(occurrence)
                record_hash = content_hash(record)
                source_hashes[side] = record_hash
                side_evidence[side] = {
                    "source_record": {
                        **{key: record[key] for key in SOURCE_FIELDS},
                        "record_sha256": record_hash,
                    },
                    "official_source_occurrences": [occurrence],
                }
            row = {
                "id": f"sponsoratlas-agent-v1-{i + 1:04d}",
                "pair_index": i + 1,
                "left_id": f"L{i:04d}",
                "right_id": f"R{i:04d}",
                "split": "holdout" if holdout else "train",
                "label": label,
                "hard_case": int(hard or not resolved),
                "label_method": "subagent",
                "origin": "subagent",
                "reviewer": "synthetic-reviewer",
                "reviewed_at": "2026-10-25T00:00:00+00:00",
                "review_model": "synthetic-model",
                "reasoning_effort": "high",
                "confidence": "medium",
                "rationale": "invented fixture",
                "label_disclosure": "Synthetic test subagent, not human or legal truth.",
                "annotation_version": 2,
                "evidence": side_evidence,
                "source_record_hashes": source_hashes,
                "provenance": {
                    "first_pass_artifact_sha256": "b" * 64,
                    "first_pass_label": label,
                    "stage": "first_pass_blinded_source_assisted_subagent_review",
                    "first_pass_reviewed_at": "2026-10-25T00:00:00+00:00",
                    "record_hash_definition": "SHA256 of entire original record dict serialized as sorted-key compact JSON; unchanged across revisions.",
                },
            }
            for side in ("left", "right"):
                row[side + "_source_system"] = "uscis"
                row[side + "_source_employer_key"] = row[side + "_id"]
            rows.append(row)
            candidates.append(
                {
                    k: row[k]
                    for k in (
                        "left_id",
                        "right_id",
                        "left_source_system",
                        "left_source_employer_key",
                        "right_source_system",
                        "right_source_employer_key",
                        "split",
                    )
                }
            )
            # 28 of 48 resolved holdout positives linked; others absent direct candidates.
            if (holdout and i < 28) or (not holdout and positive):
                edges.append(
                    {
                        "left_id": row["left_id"],
                        "right_id": row["right_id"],
                        "match_probability": 0.95,
                    }
                )
        record_schema = pa.schema(
            [
                (key, pa.bool_() if kind == "bool" else pa.string())
                for key, kind in RECORD_SCHEMA.items()
            ]
        )
        pq.write_table(pa.Table.from_pylist(records, schema=record_schema), inp / "records.parquet")
        pq.write_table(pa.Table.from_pylist(occurrences), inp / "occurrences.parquet")
        input_meta = {
            "kind": "synthetic-test-only",
            "records_sha256": digest(inp / "records.parquet"),
            "occurrences_sha256": digest(inp / "occurrences.parquet"),
            "source_records": len(records),
            "rows": len(occurrences),
            "originals": [
                {
                    "source_system": "uscis",
                    "source_release": "SYNTHETIC",
                    "source_sha256": "a" * 64,
                    "rows": len(occurrences),
                }
            ],
        }
        dump(inp / "input.json", input_meta)
        for filename in ("candidates.csv", "frozen-pairs.csv"):
            with (root / filename).open("w", newline="") as file:
                columns = (
                    list(candidates[0])
                    if filename == "candidates.csv"
                    else ["left_id", "right_id", "split"]
                )
                writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(candidates)
        write_jsonl(annotation / "agent-pairs.jsonl", rows)
        dump(
            annotation / "input-hashes.json",
            {
                str((root / "candidates.csv").relative_to(ROOT)): digest(root / "candidates.csv"),
                str((inp / "records.parquet").relative_to(ROOT)): input_meta["records_sha256"],
                str((inp / "occurrences.parquet").relative_to(ROOT)): input_meta[
                    "occurrences_sha256"
                ],
            },
        )
        counts = {
            "reviewed": 600,
            "resolved": 503,
            "positive": 206,
            "negative": 297,
            "abstained": 97,
            "hard_cases": 262,
            "resolved_hard_cases": 165,
        }
        dump(annotation / "validation.json", {**counts, "first_pass_artifact_sha256": "b" * 64})
        dump(annotation / "input-quality-findings.jsonl", {"synthetic": True})
        dump(
            annotation / "output-hashes.json",
            {
                name: digest(annotation / name)
                for name in (
                    "agent-pairs.jsonl",
                    "input-hashes.json",
                    "validation.json",
                    "input-quality-findings.jsonl",
                )
            },
        )
        dump(
            annotation / "FINAL-MANIFEST.json",
            {
                **counts,
                "version": 2,
                "status": "complete",
                "label_origin": "subagent",
                "agent_pairs_sha256": digest(annotation / "agent-pairs.jsonl"),
                "first_pass_agent_pairs_sha256": "b" * 64,
                "output_hash_manifest_sha256": digest(annotation / "output-hashes.json"),
            },
        )
        train_ids = [r["unique_id"] for r in records if int(r["unique_id"][1:]) >= 108]
        (model / "training_ids.txt").write_text("\n".join(train_ids) + "\n")
        (root / "holdout-endpoint-ids.txt").write_text(
            "\n".join(sorted(r["unique_id"] for r in records if int(r["unique_id"][1:]) < 108))
            + "\n"
        )
        dump(model / "model.json", {"synthetic": True, "not_trained": True})
        (root / "driver.py").write_text("# Synthetic fixture provenance, never executed.\n")
        disclosure = {
            "acceptance_eligible": False,
            "human_acceptance_eligible": False,
            "label_origin": "subagent",
            "research_only": True,
            "agent_eval": True,
            "threshold_tuning": False,
            "threshold": THRESHOLD,
            "git_head": "0" * 40,
            "driver_sha256": digest(root / "driver.py"),
        }
        training = {
            **disclosure,
            "seed": 42,
            "records": len(train_ids),
            "input_kind": input_meta["kind"],
            "input_records_sha256": input_meta["records_sha256"],
            "input_occurrences_sha256": input_meta["occurrences_sha256"],
            "model_sha256": digest(model / "model.json"),
            "training_ids_sha256": digest(model / "training_ids.txt"),
            "excluded_ids_sha256": digest(root / "holdout-endpoint-ids.txt"),
            "frozen_pairs_sha256": digest(root / "frozen-pairs.csv"),
        }
        dump(model / "training.json", training)
        pq.write_table(pa.Table.from_pylist(edges), pred / "scores.parquet")
        membership, _ = constrained_clusters(
            records, [(r["left_id"], r["right_id"], r["match_probability"]) for r in edges]
        )
        canonical, registry = resolve_registry(records, membership, input_meta, initialize=True)
        save_registry(pred / "registry.json", registry)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {k: r[k] for k in ("unique_id", "source_system", "source_employer_key")}
                    | {"employer_id": canonical[r["unique_id"]]}
                    for r in records
                ]
            ),
            pred / "int_employer_xref.parquet",
        )
        prediction = {
            **disclosure,
            **registry_metadata(pred / "registry.json"),
            "input_kind": input_meta["kind"],
            "threshold": THRESHOLD,
            "input_records_sha256": input_meta["records_sha256"],
            "input_occurrences_sha256": input_meta["occurrences_sha256"],
            "model_sha256": training["model_sha256"],
            "training_manifest_sha256": digest(model / "training.json"),
            "scores_sha256": digest(pred / "scores.parquet"),
            "xref_sha256": digest(pred / "int_employer_xref.parquet"),
            "source_records": len(records),
            "candidates": len(edges),
        }
        dump(pred / "prediction.json", prediction)
        dump(
            root / "source-binding.json",
            {
                "files": {
                    str(path.relative_to(ROOT)): digest(path)
                    for path in (
                        inp / "input.json",
                        inp / "records.parquet",
                        inp / "occurrences.parquet",
                        root / "candidates.csv",
                        *(ROOT / name for name in SOURCE_CODE),
                    )
                },
                "frozen_pairs_sha256": digest(root / "frozen-pairs.csv"),
                "excluded_ids_sha256": digest(root / "holdout-endpoint-ids.txt"),
                "driver_sha256": digest(root / "driver.py"),
                "git_head": "0" * 40,
            },
        )
        artifact_paths = [
            model / "model.json",
            model / "training.json",
            model / "training_ids.txt",
            pred / "registry.json",
            pred / "scores.parquet",
            pred / "int_employer_xref.parquet",
            pred / "prediction.json",
            root / "source-binding.json",
            root / "frozen-pairs.csv",
            root / "holdout-endpoint-ids.txt",
            root / "driver.py",
        ]
        artifact_hashes = {str(path.relative_to(root)): digest(path) for path in artifact_paths}
        dump(root / "artifact-binding.json", {"artifacts": artifact_hashes})
        dump(
            root / "FINAL-BINDING.json",
            {
                "git_head": "0" * 40,
                "all_artifacts_sha256": {
                    **artifact_hashes,
                    "artifact-binding.json": digest(root / "artifact-binding.json"),
                },
            },
        )
        args = {
            "input_dir": inp,
            "model_dir": model,
            "prediction_dir": pred,
            "labels_path": annotation / "agent-pairs.jsonl",
            "candidates_path": root / "candidates.csv",
            "frozen_candidates_path": root / "frozen-pairs.csv",
            "label_manifest_path": annotation / "FINAL-MANIFEST.json",
            "label_manifest_sha256": digest(annotation / "FINAL-MANIFEST.json"),
            "input_binding_path": annotation / "input-hashes.json",
            "research_binding_path": root / "FINAL-BINDING.json",
            "research_binding_sha256": digest(root / "FINAL-BINDING.json"),
            "output": root.parent / "new-report",
        }
        yield {"root": root, "args": args, "rows": rows, "candidates": candidates}


def relabel(frozen, mutate):
    rows = [json.loads(line) for line in frozen["args"]["labels_path"].read_text().splitlines()]
    mutate(rows)
    write_jsonl(frozen["args"]["labels_path"], rows)
    # Deliberately rebind the label hash to test semantic checks, not only byte integrity.
    ann = frozen["args"]["label_manifest_path"].parent
    output_hashes = json.loads((ann / "output-hashes.json").read_text())
    output_hashes["agent-pairs.jsonl"] = digest(frozen["args"]["labels_path"])
    dump(ann / "output-hashes.json", output_hashes)
    manifest = json.loads(frozen["args"]["label_manifest_path"].read_text())
    manifest["agent_pairs_sha256"] = output_hashes["agent-pairs.jsonl"]
    manifest["output_hash_manifest_sha256"] = digest(ann / "output-hashes.json")
    dump(frozen["args"]["label_manifest_path"], manifest)
    frozen["args"]["label_manifest_sha256"] = digest(frozen["args"]["label_manifest_path"])


def rebind_research(frozen, paths):
    root = frozen["root"]
    artifact = json.loads((root / "artifact-binding.json").read_text())
    final = json.loads((root / "FINAL-BINDING.json").read_text())
    for path in paths:
        key = str(path.relative_to(root))
        artifact["artifacts"][key] = final["all_artifacts_sha256"][key] = digest(path)
    dump(root / "artifact-binding.json", artifact)
    final["all_artifacts_sha256"]["artifact-binding.json"] = digest(root / "artifact-binding.json")
    dump(root / "FINAL-BINDING.json", final)
    frozen["args"]["research_binding_sha256"] = digest(root / "FINAL-BINDING.json")


def rebind_csv_probe(frozen, key):
    """Bind a real small CSV parser failure through the existing synthetic chain."""
    root = frozen["root"]
    path = frozen["args"][key]
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    rows[0]["oversized_synthetic_probe"] = "x" * (130 * 1024)
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[*rows[0]])
        writer.writeheader()
        writer.writerows(rows)
    source_path = root / "source-binding.json"
    source = json.loads(source_path.read_text())
    changed = [source_path]
    if key == "candidates_path":
        source["files"][str(path.relative_to(ROOT))] = digest(path)
        ann = frozen["args"]["label_manifest_path"].parent
        input_binding = frozen["args"]["input_binding_path"]
        inputs = json.loads(input_binding.read_text())
        inputs[str(path.relative_to(ROOT))] = digest(path)
        dump(input_binding, inputs)
        hashes = json.loads((ann / "output-hashes.json").read_text())
        hashes[input_binding.name] = digest(input_binding)
        dump(ann / "output-hashes.json", hashes)
        manifest = json.loads((ann / "FINAL-MANIFEST.json").read_text())
        manifest["output_hash_manifest_sha256"] = digest(ann / "output-hashes.json")
        dump(ann / "FINAL-MANIFEST.json", manifest)
        frozen["args"]["label_manifest_sha256"] = digest(ann / "FINAL-MANIFEST.json")
    else:
        source["frozen_pairs_sha256"] = digest(path)
        training_path = frozen["args"]["model_dir"] / "training.json"
        training = json.loads(training_path.read_text())
        training["frozen_pairs_sha256"] = digest(path)
        dump(training_path, training)
        prediction_path = frozen["args"]["prediction_dir"] / "prediction.json"
        prediction = json.loads(prediction_path.read_text())
        prediction["training_manifest_sha256"] = digest(training_path)
        dump(prediction_path, prediction)
        changed.extend([path, training_path, prediction_path])
    dump(source_path, source)
    rebind_research(frozen, changed)


def test_synthetic_low_recall_disclosure_abstentions_and_private_immutable_output(frozen):
    result = evaluate(**frozen["args"])
    assert result["status"] == "AGENT_REFERENCE_THRESHOLDS_NOT_MET"
    assert result["metrics"]["tp"] == 28 and result["metrics"]["fn"] == 20
    assert result["metrics"]["fp"] == 0 and result["metrics"]["tn"] == 47
    assert result["metrics"]["precision"] == 1 and result["metrics"]["recall"] == 28 / 48
    assert result["counts"]["resolved"] == 503 and result["counts"]["abstained"] == 97
    assert result["counts"]["resolved_hard_cases"] == 165
    assert result["holdout"]["abstained"] == 13 and result["holdout"]["resolved"] == 95
    assert result["holdout"]["all_endpoints"] == 216
    assert result["holdout"]["training_intersection"] == 0
    assert result["label_method"] == result["evidence_origin"] == "subagent"
    assert result["human_evidence"] is result["human_acceptance_eligible"] is False
    assert result["input_kind"] == "synthetic-test-only"
    assert "threshold_curve" not in result and "errors" not in result
    assert result["development"]["split"] == "train"
    assert all(int(e["left_id"][1:]) >= 108 for e in result["development"]["errors"])
    out = frozen["args"]["output"]
    assert out.stat().st_mode & 0o777 == 0o700
    assert (out / "report.json").stat().st_mode & 0o777 == 0o600
    original = (out / "report.json").read_bytes()
    with pytest.raises(ValueError, match="output"):
        evaluate(**frozen["args"])
    assert (out / "report.json").read_bytes() == original


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda rows: rows[0].update(origin="human"), "subagent"),
        (lambda rows: rows[0].update(label=True), "label"),
        (lambda rows: rows[0].update(label="1"), "label"),
        (lambda rows: rows[0].update(hard_case=True), "hard_case"),
        (lambda rows: rows[0].update(pair_index=True), "order"),
        (lambda rows: rows[0].update(id="changed"), "identity"),
        (lambda rows: rows[0].update(reviewed_at="2026-10-25"), "timezone"),
        (lambda rows: rows[0].update(split="train"), "frozen"),
        (
            lambda rows: rows[0]["provenance"].update(first_pass_artifact_sha256="c" * 64),
            "provenance",
        ),
        (
            lambda rows: rows[0]["evidence"]["left"]["source_record"].update(
                source_employer_key="fake"
            ),
            "source",
        ),
        (
            lambda rows: rows[0]["evidence"]["left"]["official_source_occurrences"][0].update(
                source_row_number=9999
            ),
            "occurrence",
        ),
        (
            lambda rows: (
                rows[0].update(label=None),
                rows[0]["provenance"].update(first_pass_label=None),
            ),
            "count",
        ),
        (lambda rows: rows[108].update(left_id=rows[0]["left_id"]), "frozen"),
    ],
)
def test_synthetic_adulterated_rows_rejected_even_if_label_manifest_rebound(frozen, mutate, match):
    relabel(frozen, mutate)
    with pytest.raises(ValueError, match=match):
        evaluate(**frozen["args"])
    assert not frozen["args"]["output"].exists()


@pytest.mark.parametrize(
    "name",
    [
        "occurrences.parquet",
        "registry.json",
        "scores.parquet",
        "int_employer_xref.parquet",
        "model.json",
    ],
)
def test_synthetic_changed_artifact_rejected(frozen, name):
    directory = (
        frozen["args"]["input_dir"]
        if name == "occurrences.parquet"
        else (
            frozen["args"]["model_dir"]
            if name == "model.json"
            else frozen["args"]["prediction_dir"]
        )
    )
    with (directory / name).open("ab") as file:
        file.write(b"changed")
    with pytest.raises(ValueError, match="digest"):
        evaluate(**frozen["args"])


def test_synthetic_abstained_holdout_training_leak_rejected_even_with_rebound_hash(frozen):
    model = frozen["args"]["model_dir"]
    ids_path = model / "training_ids.txt"
    with ids_path.open("a") as file:
        file.write("L0095\n")  # This is an ABSTAINED holdout endpoint.
    training = json.loads((model / "training.json").read_text())
    training["records"] += 1
    training["training_ids_sha256"] = digest(ids_path)
    dump(model / "training.json", training)
    pred_path = frozen["args"]["prediction_dir"] / "prediction.json"
    pred = json.loads(pred_path.read_text())
    pred["training_manifest_sha256"] = digest(model / "training.json")
    dump(pred_path, pred)
    rebind_research(frozen, [ids_path, model / "training.json", pred_path])
    with pytest.raises(ValueError, match="holdout.*training"):
        evaluate(**frozen["args"])


def test_synthetic_arbitrary_xref_rejected_after_rebinding_all_its_hashes(frozen):
    pred = frozen["args"]["prediction_dir"]
    path = pred / "int_employer_xref.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["employer_id"] = "emp_L0001"
    pq.write_table(pa.Table.from_pylist(rows), path)
    manifest = json.loads((pred / "prediction.json").read_text())
    manifest["xref_sha256"] = digest(path)
    dump(pred / "prediction.json", manifest)
    rebind_research(frozen, [path, pred / "prediction.json"])
    with pytest.raises(ValueError, match="scored canonical"):
        evaluate(**frozen["args"])


def test_synthetic_arbitrary_registry_rejected_after_rebinding_digest_and_content(frozen):
    pred = frozen["args"]["prediction_dir"]
    path = pred / "registry.json"
    registry = json.loads(path.read_text())
    # Internally valid anchor, but contradicts scored clustering of L0000/R0000.
    next(r for r in registry["payload"]["assignments"] if r["unique_id"] == "R0000")[
        "employer_id"
    ] = "emp_R0000"
    registry["content_sha256"] = content_hash(registry["payload"])
    dump(path, registry)
    manifest = json.loads((pred / "prediction.json").read_text())
    manifest.update(
        registry_sha256=digest(path), registry_content_sha256=registry["content_sha256"]
    )
    dump(pred / "prediction.json", manifest)
    rebind_research(frozen, [path, pred / "prediction.json"])
    with pytest.raises(ValueError, match="registry.*rebuild"):
        evaluate(**frozen["args"])


@pytest.mark.parametrize("kind", ["threshold", "seed", "acceptance_eligible"])
def test_synthetic_fixed_semantics_and_research_classification(frozen, kind):
    model = frozen["args"]["model_dir"]
    manifest = json.loads((model / "training.json").read_text())
    manifest[kind] = {"threshold": 0.5, "seed": True, "acceptance_eligible": True}[kind]
    dump(model / "training.json", manifest)
    pred_path = frozen["args"]["prediction_dir"] / "prediction.json"
    pred = json.loads(pred_path.read_text())
    pred["training_manifest_sha256"] = digest(model / "training.json")
    dump(pred_path, pred)
    rebind_research(frozen, [model / "training.json", pred_path])
    with pytest.raises(ValueError, match="fixed|research"):
        evaluate(**frozen["args"])


@pytest.mark.parametrize("kind", ["source", "research", "parent", "symlink", "existing", "outside"])
def test_synthetic_output_namespace_alias_rejected_without_touching_sources(frozen, kind):
    root = frozen["root"]
    if kind == "source":
        output = frozen["args"]["input_dir"] / "inside"
    elif kind == "research":
        output = root / "inside-research"
    elif kind == "parent":
        output = root
    elif kind == "symlink":
        (root / "link").symlink_to(root / "input", target_is_directory=True)
        output = root / "link" / "report"
    elif kind == "existing":
        output = root / "exists"
        output.mkdir()
    else:
        output = Path("/tmp/agent-reference-forbidden-output")
    frozen["args"]["output"] = output
    with pytest.raises(ValueError, match="output"):
        evaluate(**frozen["args"])


@pytest.mark.parametrize("key", ["label_manifest_sha256", "research_binding_sha256"])
def test_synthetic_trusted_roots_are_not_inferred_from_local_metadata(frozen, key):
    frozen["args"][key] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        evaluate(**frozen["args"])


def cli_args(args):
    return [
        token
        for key, value in args.items()
        for token in (
            "--"
            + {
                "input_dir": "input",
                "model_dir": "model",
                "prediction_dir": "predictions",
                "labels_path": "labels",
                "candidates_path": "candidates",
                "frozen_candidates_path": "frozen-candidates",
                "label_manifest_path": "label-manifest",
                "input_binding_path": "input-binding",
                "research_binding_path": "research-binding",
            }.get(key, key.replace("_", "-")),
            str(value),
        )
    ]


def test_synthetic_cli_unmet_is_exit_one_and_invalid_is_exit_two(frozen):
    command = [str(ROOT / ".venv/bin/python"), "-m", "er.agent_reference"]
    run = subprocess.run(
        command + cli_args(frozen["args"]),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert json.loads(run.stdout)["status"] == "AGENT_REFERENCE_THRESHOLDS_NOT_MET"
    run = subprocess.run(
        command + cli_args(frozen["args"]),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
    )
    assert run.returncode == 2
    assert json.loads(run.stdout)["status"] == "INVALID_AGENT_REFERENCE_EVIDENCE"


def test_synthetic_cli_met_targets_still_never_claims_human_acceptance(frozen, capsys):
    add_scores(
        frozen,
        [
            {"left_id": f"L{i:04d}", "right_id": f"R{i:04d}", "match_probability": 0.95}
            for i in range(28, 48)
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main(cli_args(frozen["args"]))
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["human_acceptance_eligible"] is False


def add_scores(frozen, added):
    pred, inp = frozen["args"]["prediction_dir"], frozen["args"]["input_dir"]
    scores = pq.read_table(pred / "scores.parquet").to_pylist() + added
    pq.write_table(pa.Table.from_pylist(scores), pred / "scores.parquet")
    records = pq.read_table(inp / "records.parquet").to_pylist()
    membership, _ = constrained_clusters(
        records, [(r["left_id"], r["right_id"], r["match_probability"]) for r in scores]
    )
    canonical, registry = resolve_registry(
        records, membership, json.loads((inp / "input.json").read_text()), initialize=True
    )
    # Synthetic fixture preparation only; consumer never writes any registry.
    dump(pred / "registry.json", registry)
    rows = pq.read_table(pred / "int_employer_xref.parquet").to_pylist()
    for row in rows:
        row["employer_id"] = canonical[row["unique_id"]]
    pq.write_table(pa.Table.from_pylist(rows), pred / "int_employer_xref.parquet")
    manifest = json.loads((pred / "prediction.json").read_text())
    manifest.update(
        registry_metadata(pred / "registry.json"),
        candidates=len(scores),
        scores_sha256=digest(pred / "scores.parquet"),
        xref_sha256=digest(pred / "int_employer_xref.parquet"),
    )
    dump(pred / "prediction.json", manifest)
    rebind_research(
        frozen,
        [
            pred / n
            for n in (
                "registry.json",
                "scores.parquet",
                "int_employer_xref.parquet",
                "prediction.json",
            )
        ],
    )


def test_synthetic_final_cluster_transitivity_counts_missing_direct_positive(frozen):
    add_scores(
        frozen,
        [
            {"left_id": "L0028", "right_id": "L0029", "match_probability": 0.95},
            {"left_id": "L0029", "right_id": "R0028", "match_probability": 0.95},
        ],
    )
    result = evaluate(**frozen["args"])
    assert result["metrics"]["tp"] == 29
    assert result["metrics"]["fn"] == 19
    assert result["metrics"]["direct_score_missing"] == 67


def inherit_registry(frozen):
    pred, inp = frozen["args"]["prediction_dir"], frozen["args"]["input_dir"]
    parent = frozen["root"] / "parent-registry.json"
    parent.write_bytes((pred / "registry.json").read_bytes())
    records = pq.read_table(inp / "records.parquet").to_pylist()
    edges = pq.read_table(pred / "scores.parquet").to_pylist()
    membership, _ = constrained_clusters(
        records, [(r["left_id"], r["right_id"], r["match_probability"]) for r in edges]
    )
    _, inherited = resolve_registry(
        records, membership, json.loads((inp / "input.json").read_text()), previous=parent
    )
    dump(pred / "registry.json", inherited)
    manifest = json.loads((pred / "prediction.json").read_text())
    manifest.update(registry_metadata(pred / "registry.json", parent))
    # Deliberately record unavailable old location to cover exact relocation override.
    manifest["registry_previous_path"] = str(frozen["root"] / "relocated-old-path.json")
    dump(pred / "prediction.json", manifest)
    rebind_research(frozen, [pred / "registry.json", pred / "prediction.json"])
    return parent


def test_synthetic_exact_previous_registry_relocation_and_no_migration(frozen):
    parent = inherit_registry(frozen)
    result = evaluate(**frozen["args"], previous_registry=parent)
    assert result["registry_context"]["mode"] == "INHERIT"
    assert result["registry_context"]["previous_sha256"] == digest(parent)
    assert result["metrics"]["tp"] == 28


def test_synthetic_wrong_previous_registry_rejected(frozen):
    parent = inherit_registry(frozen)
    with parent.open("a") as file:
        file.write("\n")  # Valid JSON, wrong exact parent artifact hash.
    with pytest.raises(ValueError, match="digest"):
        evaluate(**frozen["args"], previous_registry=parent)


@pytest.mark.parametrize("malformation", ["duplicate", "nonfinite"])
def test_synthetic_strict_json_labels_reject_duplicate_keys_and_nonfinite(frozen, malformation):
    labels = frozen["args"]["labels_path"]
    raw = labels.read_text()
    raw = (
        raw.replace('"label": 1', '"label": 1, "label": 0', 1)
        if malformation == "duplicate"
        else (raw.replace('"label": 1', '"label": NaN', 1))
    )
    labels.write_text(raw)
    ann = labels.parent
    hashes = json.loads((ann / "output-hashes.json").read_text())
    hashes["agent-pairs.jsonl"] = digest(labels)
    dump(ann / "output-hashes.json", hashes)
    manifest = json.loads((ann / "FINAL-MANIFEST.json").read_text())
    manifest.update(
        agent_pairs_sha256=digest(labels),
        output_hash_manifest_sha256=digest(ann / "output-hashes.json"),
    )
    dump(ann / "FINAL-MANIFEST.json", manifest)
    frozen["args"]["label_manifest_sha256"] = digest(ann / "FINAL-MANIFEST.json")
    with pytest.raises(ValueError, match="duplicate|nonfinite"):
        evaluate(**frozen["args"])


def test_synthetic_abstention_requires_explicit_first_pass_provenance(frozen):
    relabel(frozen, lambda rows: rows[95]["provenance"].pop("first_pass_label"))
    with pytest.raises(ValueError, match="provenance"):
        evaluate(**frozen["args"])


def second_pass(frozen, adulterate=False):
    ann = frozen["args"]["labels_path"].parent
    external = ann / "external-evidence-input-snapshot.jsonl"
    write_jsonl(external, [{"synthetic_external": True}])
    source = {
        "url": "https://www.uscis.gov/synthetic-only.html",
        "title": "Synthetic only",
        "locator": "imaginary row",
        "observed_fact": "invented test assertion",
        "verification": "synthetic test review",
        "limitation": "not actual evidence",
    }
    validation = json.loads((ann / "validation.json").read_text())
    validation.update(external_input_artifact_sha256=digest(external), changed_pairs=[1])
    dump(ann / "validation.json", validation)

    def mutate(rows):
        rows[0]["provenance"].update(
            stage="second_pass_external_source_review",
            first_pass_label=None,
            reviewer_independently_opened_primary_sources=True,
            external_input_artifact_sha256=digest(external),
        )
        rows[0]["evidence"]["supplemental_primary_sources"] = [source]

    relabel(frozen, mutate)
    row = json.loads(frozen["args"]["labels_path"].read_text().splitlines()[0])
    primary = ann / "independently-reviewed-primary-sources.jsonl"
    write_jsonl(
        primary,
        [
            {
                "pair_index": 1,
                "sources": [source],
                "origin": "subagent",
                "checked_at": row["reviewed_at"],
                "reviewer": row["reviewer"],
            }
        ],
    )
    audit = ann / "revision-audit.jsonl"
    write_jsonl(
        audit,
        [
            {
                "pair_index": 1,
                "left_id": row["left_id"],
                "right_id": row["right_id"],
                "before": {"label": None},
                "after": {k: row[k] for k in ("label", "confidence", "rationale", "reviewed_at")},
                "origin": "subagent",
                "label_method": "subagent",
                "reviewer": row["reviewer"],
                "evidence": [source],
                "first_pass_artifact_sha256": "b" * 64,
                "external_input_artifact_sha256": digest(external),
                "changed_at": row["reviewed_at"],
            }
        ],
    )
    hashes = json.loads((ann / "output-hashes.json").read_text())
    for path in (external, primary, audit, ann / "validation.json"):
        hashes[path.name] = digest(path)
    dump(ann / "output-hashes.json", hashes)
    manifest = json.loads((ann / "FINAL-MANIFEST.json").read_text())
    manifest.update(
        changed_pairs=[1], output_hash_manifest_sha256=digest(ann / "output-hashes.json")
    )
    dump(ann / "FINAL-MANIFEST.json", manifest)
    frozen["args"]["label_manifest_sha256"] = digest(ann / "FINAL-MANIFEST.json")
    if adulterate:
        relabel(
            frozen,
            lambda rows: rows[0]["evidence"]["supplemental_primary_sources"][0].update(
                observed_fact="new unsupported assertion not in frozen primary-source review"
            ),
        )


def test_synthetic_second_pass_requires_exact_frozen_primary_and_revision_semantics(frozen):
    second_pass(frozen, adulterate=True)
    with pytest.raises(ValueError, match="primary.*provenance|revision.*provenance"):
        evaluate(**frozen["args"])


def test_synthetic_valid_second_pass_cross_manifests_consumed(frozen):
    second_pass(frozen)
    assert evaluate(**frozen["args"])["metrics"]["tp"] == 28


def test_synthetic_endpoint_cannot_cross_even_when_all_candidate_splits_agree(frozen):
    rows, candidates = copy.deepcopy(frozen["rows"]), copy.deepcopy(frozen["candidates"])
    rows[108]["left_id"] = candidates[108]["left_id"] = rows[0]["left_id"]
    rows[108]["left_source_employer_key"] = candidates[108]["left_source_employer_key"] = rows[0][
        "left_id"
    ]
    ann = frozen["args"]["label_manifest_path"].parent
    with pytest.raises(ValueError, match="crosses frozen split"):
        validate_references(
            rows,
            candidates,
            candidates,
            json.loads((ann / "FINAL-MANIFEST.json").read_text()),
            json.loads((ann / "validation.json").read_text()),
        )


@pytest.mark.parametrize("kind", ["resolved", "hard"])
def test_synthetic_joint_reference_coverage_minima_are_not_relaxed(frozen, kind):
    rows = copy.deepcopy(frozen["rows"])
    if kind == "resolved":
        for row in rows[108:112]:
            row["label"] = row["provenance"]["first_pass_label"] = None
    else:
        for row in rows:
            row["hard_case"] = 0
    counts = label_counts(rows)
    ann = frozen["args"]["label_manifest_path"].parent
    manifest = {**json.loads((ann / "FINAL-MANIFEST.json").read_text()), **counts}
    validation = {**json.loads((ann / "validation.json").read_text()), **counts}
    with pytest.raises(ValueError, match="requires500 resolved"):
        validate_references(rows, frozen["candidates"], frozen["candidates"], manifest, validation)


def test_synthetic_research_frozen_split_is_three_column_projection(frozen):
    ann = frozen["args"]["label_manifest_path"].parent
    projection = [
        {key: row[key] for key in ("left_id", "right_id", "split")} for row in frozen["candidates"]
    ]
    result = validate_references(
        frozen["rows"],
        frozen["candidates"],
        projection,
        json.loads((ann / "FINAL-MANIFEST.json").read_text()),
        json.loads((ann / "validation.json").read_text()),
    )
    assert result["resolved"] == 503


def test_synthetic_optional_research_source_key_cannot_conflict(frozen):
    ann = frozen["args"]["label_manifest_path"].parent
    projection = [
        {key: row[key] for key in ("left_id", "right_id", "split")} for row in frozen["candidates"]
    ]
    projection[0]["left_source_employer_key"] = "conflicting-key"
    with pytest.raises(ValueError, match="frozen"):
        validate_references(
            frozen["rows"],
            frozen["candidates"],
            projection,
            json.loads((ann / "FINAL-MANIFEST.json").read_text()),
            json.loads((ann / "validation.json").read_text()),
        )


def test_synthetic_annotation_ascii_serialization_matches_independent_golden():
    # Literal canonical bytes and fixed SHA were derived independently of the helper.
    golden = b'{"city":"montr\\u00e9al","eligible":true,"tax_id":null}'
    expected = "874083f9f1afc0a426e6f898eaeca13a67f007061c32f31600a3dcf030067620"
    assert hashlib.sha256(golden).hexdigest() == expected
    assert (
        annotation_record_hash({"tax_id": None, "eligible": True, "city": "montréal"}) == expected
    )


def test_synthetic_non_ascii_contract_hashes_full_actual_record_not_snapshot(frozen):
    records = pq.read_table(frozen["args"]["input_dir"] / "records.parquet").to_pylist()[:2]
    row = copy.deepcopy(frozen["rows"][0])
    records[0]["city"] = "montréal"
    row["evidence"]["left"]["source_record"]["city"] = "montréal"
    occurrence = row["evidence"]["left"]["official_source_occurrences"][0]
    occurrence["city"] = "montréal"
    # Independent existing annotation contract; no call to the function under test.
    expected = hashlib.sha256(
        json.dumps(
            records[0], sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    row["source_record_hashes"]["left"] = expected
    row["evidence"]["left"]["source_record"]["record_sha256"] = expected
    assert len(records[0]) == 15
    assert len(row["evidence"]["left"]["source_record"]) == 13  # 12 fields plus stored hash.
    occurrences = [
        row["evidence"][side]["official_source_occurrences"][0] for side in ("left", "right")
    ]
    path = frozen["root"] / "unicode-occurrences.parquet"
    pq.write_table(pa.Table.from_pylist(occurrences), path)
    metadata = {
        "rows": 2,
        "originals": [
            {
                "source_system": "uscis",
                "source_release": "SYNTHETIC",
                "source_sha256": "a" * 64,
                "rows": 2,
            }
        ],
    }
    assert (
        verify_source_evidence([row], records, path, metadata)[records[0]["unique_id"]]
        == records[0]
    )
    # Hidden normalized columns must also be hash-bound, even if the snapshot omits them.
    records[0]["name_norm"] = "adulterated hidden normalized attribute"
    with pytest.raises(ValueError, match="record hash"):
        verify_source_evidence([row], records, path, metadata)


@pytest.mark.parametrize("key", ["candidates_path", "frozen_candidates_path"])
def test_synthetic_bound_csv_parser_failure_is_invalid_evidence_exit_two(frozen, key):
    assert csv.field_size_limit() < 130 * 1024
    rebind_csv_probe(frozen, key)
    run = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "er.agent_reference", *cli_args(frozen["args"])],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert run.returncode == 2, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "INVALID_AGENT_REFERENCE_EVIDENCE"
    assert "CSV" in payload["error"]
    assert payload["human_evidence"] is payload["human_acceptance_eligible"] is False
    assert not frozen["args"]["output"].exists()


@pytest.mark.parametrize(
    "case",
    [
        "initialize_outside_parent",
        "initialize_db_parent",
        "initialize_empty_parent",
        "initialize_false_parent",
        "initialize_digest_only",
        "initialize_digest_and_parent",
        "inherit_missing_digest",
        "inherit_invalid_digest",
        "inherit_missing_path",
        "inherit_missing_recorded_path_with_override",
        "inherit_outside_parent",
        "inherit_db_parent",
        "unknown_mode",
    ],
)
def test_synthetic_registry_parent_combinations_fail_before_any_helper_or_parent_read(
    frozen, monkeypatch, case
):
    import er.agent_reference as consumer
    from er import registry

    # These paths are fictional. Spies intercept any attempted digest before I/O;
    # no DB or external file is created, opened, hashed or even required to exist.
    forbidden = Path("/outside-project/er-parent-do-not-read.duckdb")
    inside_db = frozen["root"] / "er-parent-do-not-read.duckdb"
    prediction_path = frozen["args"]["prediction_dir"] / "prediction.json"
    override = None
    if case == "inherit_missing_recorded_path_with_override":
        override = inherit_registry(frozen)
    prediction = json.loads(prediction_path.read_text())
    mode, sha, path = "INITIALIZE", None, None
    if case == "initialize_outside_parent":
        path = str(forbidden)
    elif case == "initialize_db_parent":
        path = str(inside_db)
    elif case == "initialize_empty_parent":
        path = ""
    elif case == "initialize_false_parent":
        path = False
    elif case == "initialize_digest_only":
        sha = "a" * 64
    elif case == "initialize_digest_and_parent":
        sha, path = "a" * 64, str(forbidden)
    elif case.startswith("inherit"):
        mode, sha, path = "INHERIT", "a" * 64, str(forbidden)
        if case == "inherit_missing_digest":
            sha = None
        elif case == "inherit_invalid_digest":
            sha = "not-a-sha256"
        elif case == "inherit_missing_path":
            path = None
        elif case == "inherit_missing_recorded_path_with_override":
            sha, path = digest(override), None
        elif case == "inherit_db_parent":
            path = str(inside_db)
    else:
        mode = "UNKNOWN"
    prediction.update(registry_mode=mode, registry_previous_sha256=sha, registry_previous_path=path)
    dump(prediction_path, prediction)
    rebind_research(frozen, [prediction_path])
    reads, helper_calls = [], []
    original_digest = consumer.digest
    original_rebuild = consumer.rebuild_registry
    watched = {forbidden, inside_db}
    if override is not None:
        watched.add(override)

    def guarded_digest(value):
        if Path(value) in watched:
            reads.append(str(value))
            return "a" * 64  # In-memory response only; never read the watched path.
        return original_digest(value)

    def watched_rebuild(*args, **kwargs):
        helper_calls.append(True)
        return original_rebuild(*args, **kwargs)

    monkeypatch.setattr(consumer, "digest", guarded_digest)
    monkeypatch.setattr(registry, "digest", guarded_digest)
    monkeypatch.setattr(consumer, "rebuild_registry", watched_rebuild)
    with pytest.raises(ValueError):
        evaluate(**frozen["args"], previous_registry=override)
    assert reads == [], "invalid parent metadata reached a file digest"
    assert helper_calls == [], "invalid parent combination reached the shared registry helper"
    assert not frozen["args"]["output"].exists()


def test_synthetic_inherit_recorded_parent_without_override_preserves_low_recall(frozen):
    parent = inherit_registry(frozen)
    path = frozen["args"]["prediction_dir"] / "prediction.json"
    prediction = json.loads(path.read_text())
    prediction["registry_previous_path"] = str(parent)
    dump(path, prediction)
    rebind_research(frozen, [path])
    result = evaluate(**frozen["args"])
    assert result["registry_context"]["mode"] == "INHERIT"
    assert result["registry_context"]["previous_sha256"] == digest(parent)
    assert result["status"] == "AGENT_REFERENCE_THRESHOLDS_NOT_MET"
    assert result["metrics"]["precision"] == 1.0 and result["metrics"]["recall"] == 28 / 48
