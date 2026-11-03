import json
import os
from pathlib import Path

import pytest

from er.evaluate import evaluate, evaluate_pairs, validate_labels
from er.predict import constrained_clusters


def test_synthetic_precision_recall_mathematics():
    # Synthetic mathematical regression only; this is NOT human employer acceptance.
    truth = json.loads((Path(__file__).parent / "fixtures/math_truth.json").read_text())["labels"]
    m = evaluate_pairs(truth, {("a", "b"): 0.95, ("a", "c"): 0.1}, 0.9)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 0, 0)
    assert m["precision"] >= 0.97 and m["recall"] >= 0.90


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SPONSORATLAS_ER_ACCEPTANCE") != "1",
    reason="opt-in: real human-label employer acceptance (SPONSORATLAS_ER_ACCEPTANCE=1)",
)
def test_evaluate_meets_thresholds():
    required = ["INPUT", "MODEL", "PREDICTIONS", "LABELS", "OUTPUT"]
    paths = {}
    for name in required:
        env = "SPONSORATLAS_ER_ACCEPTANCE_" + name
        assert os.environ.get(env), f"opt-in acceptance requires {env}; no fixture fallback"
        paths[name] = os.environ[env]
    result = evaluate(
        paths["INPUT"],
        paths["MODEL"],
        paths["PREDICTIONS"],
        paths["LABELS"],
        paths["OUTPUT"],
        os.environ.get("SPONSORATLAS_ER_ACCEPTANCE_PREVIOUS_REGISTRY"),
    )
    from er.settings import THRESHOLD

    assert result["threshold"] == THRESHOLD
    assert result["metrics"]["precision"] >= 0.97
    assert result["metrics"]["recall"] >= 0.90
    assert result["status"] == "PASS"


def test_missing_prediction_is_false_negative():
    m = evaluate_pairs([{"left_id": "a", "right_id": "b", "label": "1", "hard_case": "0"}], {}, 0.9)
    assert m["fn"] == 1 and m["recall"] == 0 and m["precision"] == 0


def test_missing_and_machine_labels_fail():
    with pytest.raises(ValueError, match="500"):
        validate_labels([])
    with pytest.raises(ValueError, match="human"):
        validate_labels(
            [
                {
                    "left_id": "a",
                    "right_id": "b",
                    "label": "1",
                    "hard_case": "1",
                    "label_method": "rule",
                    "reviewer": "bot",
                    "reviewed_at": "2026-10-23",
                    "evidence": "EIN",
                    "rationale": "same",
                    "split": "holdout",
                }
            ]
            * 500
        )


def record(uid, strong=None, **kw):
    return dict(
        unique_id=uid,
        strong_id=strong,
        country="US",
        state="CA",
        city="austin",
        postcode="11111",
        tax_last4=None,
        eligible=True,
        **kw,
    )


def test_strong_conflict_homonym_and_transitive_bridge_are_rejected():
    rows = [record("a", "US_EIN:123456789"), record("b"), record("c", "US_EIN:987654321")]
    edges = [("a", "b", 0.99), ("b", "c", 0.98), ("a", "c", 1.0)]
    members, decisions = constrained_clusters(rows, edges, 0.9)
    assert members["a"] == members["b"] and members["a"] != members["c"]
    assert any(d["reason"] == "STRONG_ID_CONFLICT" for d in decisions)
    same_name = [record("x"), dict(record("y"), city="other", postcode="22222")]
    m, d = constrained_clusters(same_name, [("x", "y", 0.9999)], 0.9)
    assert m["x"] != m["y"] and d[0]["reason"] == "INSUFFICIENT_CONTEXT"


def test_clustering_is_order_independent_and_country_conflicts_are_vetoed():
    rows = [record("a"), dict(record("b"), country="CA")]
    a, _ = constrained_clusters(rows, [("a", "b", 0.999)], 0.9)
    b, _ = constrained_clusters(list(reversed(rows)), [("b", "a", 0.999)], 0.9)
    assert a == b and a["a"] != a["b"]
