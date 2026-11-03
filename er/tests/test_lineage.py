"""Preflight rejection uses clearly synthetic bytes, never employer truth."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from er.evaluate import evaluate
from er.io import TOOLS, digest, dump
from er.lineage import validate_input
from er.predict import predict
from er.train import train


@pytest.fixture
def artifacts():
    root = TOOLS / "er-tests"
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=root))
    inp = work / "input"
    model = work / "model"
    pred = work / "pred"
    for path in (inp, model, pred):
        path.mkdir()
    (inp / "records.parquet").write_bytes(b"SYNTHETIC_PRECHECK_RECORDS_NOT_PARQUET")
    (inp / "occurrences.parquet").write_bytes(b"SYNTHETIC_PRECHECK_ORIGINAL_LINEAGE_NOT_PARQUET")
    metadata = {
        "kind": "SYNTHETIC_TEST_ONLY",
        "records_sha256": digest(inp / "records.parquet"),
        "occurrences_sha256": digest(inp / "occurrences.parquet"),
    }
    dump(inp / "input.json", metadata)
    (model / "model.json").write_text('{"kind":"SYNTHETIC_PRECHECK_NOT_A_MODEL"}')
    manifest = {
        "input_records_sha256": metadata["records_sha256"],
        "input_occurrences_sha256": metadata["occurrences_sha256"],
        "model_sha256": digest(model / "model.json"),
    }
    dump(model / "training.json", manifest)
    dump(pred / "prediction.json", manifest)
    yield work, inp, model, pred, metadata
    shutil.rmtree(work)


@pytest.mark.parametrize("boundary", ["train", "predict", "evaluate", "free"])
def test_only_replacing_occurrences_fails_before_engine_or_publication(artifacts, boundary):
    work, inp, model, pred, metadata = artifacts
    original_model = digest(model / "model.json")
    (inp / "occurrences.parquet").write_bytes(b"SYNTHETIC_MIXED_OTHER_BATCH")
    output = work / "refused"
    with pytest.raises(ValueError, match="occurrences digest mismatch"):
        if boundary == "train":
            train(inp, output)
        elif boundary == "predict":
            predict(inp, model, output, initialize_registry=True)
        elif boundary == "evaluate":
            evaluate(inp, model, pred, work / "missing-labels.csv", output)
        else:
            pytest.importorskip("pyspark")
            from er.databricks_run import run_free

            run_free(object(), input_dir=inp, output_dir=output, initialize_registry=True)
    assert not output.exists()
    assert digest(inp / "records.parquet") == metadata["records_sha256"]
    assert digest(model / "model.json") == original_model


def test_rebinding_input_manifest_cannot_rebind_trained_original_lineage(artifacts):
    _, inp, model, pred, metadata = artifacts
    (inp / "occurrences.parquet").write_bytes(b"SYNTHETIC_OTHER_BATCH")
    metadata["occurrences_sha256"] = digest(inp / "occurrences.parquet")
    dump(inp / "input.json", metadata)
    with pytest.raises(ValueError, match="model/occurrences lineage"):
        validate_input(inp, training=json.loads((model / "training.json").read_text()))
    with pytest.raises(ValueError, match="prediction/occurrences lineage"):
        validate_input(inp, prediction=json.loads((pred / "prediction.json").read_text()))
