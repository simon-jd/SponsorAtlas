"""Cluster-level counting in er.recall_experiment.metrics."""

from er.recall_experiment import metrics
from er.settings import CURVE, THRESHOLD


def test_match_threshold_is_the_user_decision():
    assert THRESHOLD == 0.875
    assert THRESHOLD in CURVE


def test_cluster_membership_counts_and_abstentions():
    membership = {"a": "a", "b": "a", "c": "a", "d": "d", "e": "e"}
    decisions = [{"left_id": "a", "right_id": "c", "reason": "BELOW_THRESHOLD"}]
    pairs = [
        {"left_id": "a", "right_id": "b", "label": 1, "hard_case": False},
        # Transitively clustered without a direct edge still counts as predicted match.
        {"left_id": "c", "right_id": "b", "label": 1, "hard_case": True},
        {"left_id": "a", "right_id": "c", "label": 0, "hard_case": True},
        {"left_id": "d", "right_id": "e", "label": 1, "hard_case": False},
        {"left_id": "d", "right_id": "a", "label": 0, "hard_case": False},
        {"left_id": "e", "right_id": "a", "label": None, "hard_case": False},
    ]
    m = metrics(pairs, membership, decisions)
    assert (m["TP"], m["FP"], m["FN"], m["TN"], m["abstained"]) == (2, 1, 1, 1, 1)
    assert m["precision"] == m["recall"] == 2 / 3
    assert m["hard_cases"]["TP"] == m["hard_cases"]["FP"] == 1
    assert m["errors_by_direct_reason"] == {"FN:NO_CANDIDATE": 1, "FP:BELOW_THRESHOLD": 1}
