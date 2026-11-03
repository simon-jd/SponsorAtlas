from er.label_workflow import assign_splits


def test_frozen_seeded_split_keeps_connected_records_together():
    pairs = [
        {"left_id": "a", "right_id": "b"},
        {"left_id": "b", "right_id": "c"},
        {"left_id": "d", "right_id": "e"},
    ]
    split = assign_splits(pairs)
    assert split[0]["split"] == split[1]["split"]
    assert {tuple(sorted((r["left_id"], r["right_id"]))): r["split"] for r in split} == {
        tuple(sorted((r["left_id"], r["right_id"]))): r["split"]
        for r in assign_splits(
            list(reversed([{"left_id": r["left_id"], "right_id": r["right_id"]} for r in pairs]))
        )
    }
    assert all("label" not in r and "label_method" not in r for r in split)
