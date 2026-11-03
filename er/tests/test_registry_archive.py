"""Rebuilds inherit the archived employer registry, so employer IDs survive them."""

import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from er.io import TOOLS, digest, dump
from er.registry_archive import latest, publish, run
from er.tests.fixtures.synthetic import records
from er.train import train


@pytest.fixture
def sandbox():
    root = TOOLS / "er-tests"
    root.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=root))
    yield path
    shutil.rmtree(path)


def _input_and_model(sandbox):
    inp = sandbox / "input"
    inp.mkdir()
    rows = records()
    pq.write_table(pa.Table.from_pylist(rows), inp / "records.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"unique_id": r["unique_id"], "source_sha256": "SYNTHETIC", "source_row_number": i}
                for i, r in enumerate(rows)
            ]
        ),
        inp / "occurrences.parquet",
    )
    dump(
        inp / "input.json",
        {
            "kind": "SYNTHETIC_TEST_ONLY",
            "records_sha256": digest(inp / "records.parquet"),
            "occurrences_sha256": digest(inp / "occurrences.parquet"),
        },
    )
    train(inp, sandbox / "model")
    return inp, sandbox / "model"


def test_rebuild_inherits_the_latest_registry_and_keeps_employer_ids(sandbox):
    inp, model = _input_and_model(sandbox)
    archive = sandbox / "archive"
    with pytest.raises(ValueError, match="--initialize-registry"):
        run(inp, model, sandbox / "refused", archive)
    assert not (sandbox / "refused").exists()

    first = run(inp, model, sandbox / "p1", archive, initialize_registry=True)
    assert first["registry_mode"] == "INITIALIZE"
    assert latest(archive) == archive / f"{first['registry_sha256']}.json"

    second = run(inp, model, sandbox / "p2", archive)
    assert second["registry_mode"] == "INHERIT"
    assert second["registry_previous_path"] == str(
        (archive / f"{first['registry_sha256']}.json").resolve()
    )
    assert latest(archive) == archive / f"{second['registry_sha256']}.json"
    ids = [
        pq.read_table(sandbox / p / "int_employer_xref.parquet").to_pylist() for p in ("p1", "p2")
    ]
    assert ids[0] == ids[1]


def test_archive_is_immutable_and_checks_names(sandbox):
    inp, model = _input_and_model(sandbox)
    archive = sandbox / "archive"
    summary = run(inp, model, sandbox / "p1", archive, initialize_registry=True)
    archived = archive / f"{summary['registry_sha256']}.json"
    assert publish(sandbox / "p1" / "registry.json", archive) == archived
    assert sorted(p.name for p in archive.iterdir()) == ["LATEST", archived.name]
    archived.write_text(archived.read_text().replace("SponsorAtlas", "Sponsoratlas", 1))
    with pytest.raises(ValueError, match="does not match its name"):
        latest(archive)
    with pytest.raises(ValueError, match="does not match its name"):
        publish(sandbox / "p1" / "registry.json", archive)


def test_empty_archive_has_no_latest(tmp_path):
    assert latest(tmp_path / "none") is None
