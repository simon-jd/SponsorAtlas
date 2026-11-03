"""Synthetic identity lifecycle regression; no human match truth."""

import json

import pytest

from er.registry import IdentityReviewRequired, load_registry, resolve_registry, save_registry

HASHES = {"records_sha256": "a" * 64, "occurrences_sha256": "b" * 64}


def row(uid, eligible=True):
    return {"unique_id": uid, "eligible": eligible}


def initial(tmp_path, rows, members):
    ids, registry = resolve_registry(rows, members, HASHES, initialize=True)
    path = tmp_path / "registry.json"
    save_registry(path, registry)
    return ids, path


def test_new_smaller_source_keeps_prior_identity_and_new_component_is_new(tmp_path):
    ids, path = initial(tmp_path, [row("b")], {"b": "b"})
    rows = [row("a"), row("b"), row("z")]
    members = {"a": "a", "b": "a", "z": "z"}
    now, registry = resolve_registry(rows, members, HASHES, previous=path)
    assert now["a"] == now["b"] == ids["b"] and now["z"] != ids["b"]
    repeat, again = resolve_registry(rows, members, HASHES, previous=path)
    assert repeat == now and again == registry


def test_merge_and_split_require_review(tmp_path):
    _, path = initial(tmp_path, [row("a"), row("b")], {"a": "a", "b": "b"})
    with pytest.raises(IdentityReviewRequired, match="MERGE"):
        resolve_registry([row("a"), row("b")], {"a": "a", "b": "a"}, HASHES, previous=path)
    _ids, joined = resolve_registry(
        [row("a"), row("b")], {"a": "a", "b": "a"}, HASHES, initialize=True
    )
    path2 = tmp_path / "joined.json"
    save_registry(path2, joined)
    with pytest.raises(IdentityReviewRequired, match="SPLIT"):
        resolve_registry([row("a"), row("b")], {"a": "a", "b": "b"}, HASHES, previous=path2)


def test_retraction_retains_history_and_invalid_old_source_fails(tmp_path):
    ids, path = initial(tmp_path, [row("b")], {"b": "b"})
    _, absent = resolve_registry([], {}, HASHES, previous=path)
    path2 = tmp_path / "absent.json"
    save_registry(path2, absent)
    restored, _ = resolve_registry([row("b")], {"b": "b"}, HASHES, previous=path2)
    assert restored == ids
    with pytest.raises(IdentityReviewRequired, match="RETRACTION_REPLACEMENT"):
        resolve_registry([row("new")], {"new": "new"}, HASHES, previous=path2)
    with pytest.raises(IdentityReviewRequired, match="UNRESOLVED"):
        resolve_registry([row("b", False)], {"b": "b"}, HASHES, previous=path)


def test_initialization_is_explicit_and_corrupt_prior_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        resolve_registry([row("a")], {"a": "a"}, HASHES)
    _, path = initial(tmp_path, [row("b")], {"b": "b"})
    data = json.loads(path.read_text())
    data["payload"]["assignments"][0]["employer_id"] = "emp_bad"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="registry"):
        load_registry(path)


def test_evaluation_rebuild_uses_inherited_ids_and_exact_parent_provenance(tmp_path):
    from er.registry import rebuild_registry, registry_metadata

    _, parent = initial(tmp_path, [row("b")], {"b": "b"})
    rows = [row("a"), row("b")]
    members = {"a": "a", "b": "a"}
    canonical, envelope = resolve_registry(rows, members, HASHES, previous=parent)
    current = tmp_path / "published"
    current.mkdir()
    save_registry(current / "registry.json", envelope)
    prediction = registry_metadata(current / "registry.json", parent)
    actual, rebuilt, published = rebuild_registry(rows, members, HASHES, prediction, current)
    assert actual == canonical == {"a": "emp_b", "b": "emp_b"} and rebuilt == published
    parent.write_text(parent.read_text() + "\n")  # Valid JSON, wrong immutable artifact bytes.
    with pytest.raises(ValueError, match="previous registry provenance"):
        rebuild_registry(rows, members, HASHES, prediction, current)
