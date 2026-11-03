"""er.label_alignment and er.threshold_report on synthetic records, scores and labels."""

import csv

import pyarrow as pa
import pyarrow.parquet as pq

from er.label_alignment import align
from er.threshold_report import compare, partition_changes


def record(uid, key, *, state="CA", city="SF", name="acme", eligible=True):
    return {
        "unique_id": uid,
        "source_system": "lca",
        "source_employer_key": key,
        "name_norm": name,
        "state": state,
        "city": city,
        "postcode": None,
        "strong_id": None,
        "tax_last4": None,
        "country": None,
        "eligible": eligible,
    }


RECORDS = [record("a", "ka"), record("b", "kb"), record("c", "kc"), record("d", "kd")]
# a-b at 0.86 is accepted at 0.85 only; b-c at 0.88 at 0.85 and 0.875; c-d at 0.95 everywhere.
EDGES = [("a", "b", 0.86), ("b", "c", 0.88), ("c", "d", 0.95)]


def pair(left, right, label, split="holdout", **extra):
    return {"left_id": left, "right_id": right, "label": label, "hard_case": False,
            "split": split, **extra}  # fmt: skip


def test_align_reports_absent_ids_and_source_key_mismatch():
    pairs = [
        pair(
            "a",
            "b",
            1,
            left_source_system="lca",
            left_source_employer_key="ka",
            right_source_system="lca",
            right_source_employer_key="kb",
        ),  # fmt: skip
        pair("a", "z", 1),
        pair("y", "z", 0),
        pair("a", "c", 0, left_source_system="lca", left_source_employer_key="OTHER"),
    ]
    result = align(pairs, RECORDS)
    assert result["pairs"] == 4 and result["matched"] == 1
    assert result["unmatched_by_reason"] == {
        "RIGHT_ABSENT": 1,
        "BOTH_ABSENT": 1,
        "LEFT_SOURCE_KEY_MISMATCH": 1,
    }
    assert [u["index"] for u in result["unmatched"]] == [1, 2, 3]


def test_compare_counts_each_threshold_from_the_same_scores():
    labels = [pair("a", "b", 1), pair("b", "c", 1), pair("c", "d", 1), pair("a", "d", 0),
              pair("a", "x", 1), pair("b", "d", 1, split="train")]  # fmt: skip
    report = compare(RECORDS, EDGES, labels, thresholds=(0.85, 0.875, 0.9))
    t = report["thresholds"]
    # At 0.85 a,b,c,d form one cluster, so the a-d negative becomes FP.
    assert {k: t["0.85"]["holdout"][k] for k in ("TP", "FP", "FN", "TN")} == {
        "TP": 3, "FP": 1, "FN": 0, "TN": 0}  # fmt: skip
    assert {k: t["0.875"]["holdout"][k] for k in ("TP", "FP", "FN", "TN")} == {
        "TP": 2, "FP": 0, "FN": 1, "TN": 1}  # fmt: skip
    assert t["0.875"]["holdout"]["precision"] == 1.0
    assert t["0.875"]["holdout"]["recall"] == 2 / 3
    assert t["0.9"]["holdout"]["TP"] == 1
    assert (t["0.85"]["accepted_edges"], t["0.875"]["accepted_edges"]) == (3, 2)
    assert (t["0.85"]["clusters"], t["0.875"]["clusters"], t["0.9"]["clusters"]) == (1, 2, 3)
    assert t["0.85"]["employers"] == 1
    # The pair with an endpoint missing from the input is reported, not counted.
    assert report["label_alignment"]["matched"] == 5
    assert t["0.875"]["train"]["TP"] == 1


def test_partition_changes_between_two_memberships():
    before = {"a": "a", "b": "a", "c": "a", "d": "a"}
    after = {"a": "a", "b": "b", "c": "b", "d": "b"}
    assert partition_changes(before, after) == {
        "records_moved": 4, "clusters_removed": 1, "clusters_added": 2}  # fmt: skip
    assert partition_changes(before, before) == {
        "records_moved": 0, "clusters_removed": 0, "clusters_added": 0}  # fmt: skip


def test_cli_writes_report(tmp_path, monkeypatch):
    import er.threshold_report as tr

    monkeypatch.setattr(tr, "run_dir", lambda p, **_: p)
    inp, pred = tmp_path / "input", tmp_path / "prediction"
    inp.mkdir()
    pred.mkdir()
    pq.write_table(pa.Table.from_pylist(RECORDS), inp / "records.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [{"left_id": a, "right_id": b, "match_probability": p} for a, b, p in EDGES]
        ),
        pred / "scores.parquet",
    )
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="") as f:
        w = csv.DictWriter(f, ["left_id", "right_id", "label", "hard_case", "split"])
        w.writeheader()
        w.writerow({"left_id": "a", "right_id": "b", "label": "1", "hard_case": "0",
                    "split": "holdout"})  # fmt: skip
    out = tmp_path / "report.json"
    report = tr.run(inp, pred, labels, out)
    assert out.exists()
    assert report["deployed_threshold"] == 0.875
    assert set(report["thresholds"]) == {"0.85", "0.875", "0.9"}
