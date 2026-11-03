"""Immutable canonical identities. Merge/split decisions require human migration review."""

import hashlib
import json
import re
from pathlib import Path

from er.io import digest
from er.settings import MAX_RECORDS

NAMESPACE = "SponsorAtlas.employer.v1"


class IdentityReviewRequired(ValueError):
    def __init__(self, reason, details):
        self.status = "IDENTITY_REVIEW_REQUIRED"
        self.details = details
        super().__init__(
            f"{reason}: {json.dumps(details, sort_keys=True)}; preserve previous registry and review migration"
        )


def content_hash(payload):
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_registry(envelope):
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("version")) is not int
        or envelope.get("version") != 1
        or set(envelope) != {"version", "payload", "content_sha256"}
    ):
        raise ValueError("invalid registry version")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or content_hash(payload) != envelope.get("content_sha256"):
        raise ValueError("registry content hash mismatch")
    if payload.get("namespace") != NAMESPACE:
        raise ValueError("invalid registry namespace")
    if set(payload) != {
        "namespace",
        "input_records_sha256",
        "input_occurrences_sha256",
        "previous_registry_sha256",
        "assignments",
    }:
        raise ValueError("invalid registry payload fields")
    for key in ("input_records_sha256", "input_occurrences_sha256"):
        if not re.fullmatch("[0-9a-f]{64}", str(payload.get(key, ""))):
            raise ValueError("invalid registry input lineage")
    parent = payload.get("previous_registry_sha256")
    if parent is not None and not re.fullmatch("[0-9a-f]{64}", str(parent)):
        raise ValueError("invalid registry parent digest")
    assignments = payload.get("assignments")
    if not isinstance(assignments, list) or len(assignments) > MAX_RECORDS:
        raise ValueError("invalid registry assignments or bounded history limit exceeded")
    seen = {}
    for item in assignments:
        if not isinstance(item, dict) or set(item) != {"unique_id", "employer_id", "active"}:
            raise ValueError("invalid registry assignment fields")
        uid = item["unique_id"]
        employer = item["employer_id"]
        if (
            not isinstance(uid, str)
            or not re.fullmatch("[A-Za-z0-9_-]{1,128}", uid)
            or uid in seen
            or type(item["active"]) is not bool
        ):
            raise ValueError("invalid or duplicate registry source ID")
        if employer is not None and (
            not isinstance(employer, str) or not re.fullmatch("emp_[A-Za-z0-9_-]{1,128}", employer)
        ):
            raise ValueError("invalid registry employer ID")
        seen[uid] = employer
    for employer in set(seen.values()) - {None}:
        if seen.get(employer[4:]) != employer:
            raise ValueError("registry canonical anchor missing or inconsistent")
    return envelope


def load_registry(path):
    try:
        if Path(path).stat().st_size > 128 * 1024 * 1024:
            raise ValueError("registry exceeds bounded artifact size")
        return validate_registry(json.loads(Path(path).read_text()))
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid previous registry: {exc}") from exc


def save_registry(path, envelope):
    validate_registry(envelope)
    path = Path(path)
    try:
        with path.open("x") as file:
            file.write(json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except FileExistsError as exc:
        raise ValueError("refusing to overwrite immutable registry") from exc


def registry_request(initialize, previous):
    if bool(initialize) == bool(previous):
        raise ValueError("choose exactly one explicit registry initialization or previous registry")
    if previous:
        load_registry(previous)
        return digest(previous)
    return None


def resolve_registry(records, membership, input_metadata, *, initialize=False, previous=None):
    previous_digest = registry_request(initialize, previous)
    previous_envelope = load_registry(previous) if previous else None
    if previous and digest(previous) != previous_digest:
        raise ValueError("previous registry changed during validation")
    old = (
        {r["unique_id"]: r["employer_id"] for r in previous_envelope["payload"]["assignments"]}
        if previous_envelope
        else {}
    )
    rows = {r["unique_id"]: r for r in records}
    if (
        len(rows) != len(records)
        or set(membership) != set(rows)
        or any(root not in rows or membership[root] != root for root in membership.values())
    ):
        raise ValueError("registry requires complete unique input membership")
    components = {}
    for uid, row in rows.items():
        if not row["eligible"]:
            if old.get(uid):
                raise IdentityReviewRequired(
                    "UNRESOLVED_PRIOR_IDENTITY", {"unique_id": uid, "employer_id": old[uid]}
                )
            continue
        components.setdefault(membership[uid], []).append(uid)
    prior_components = {}
    for component, uids in components.items():
        prior_ids = {old[uid] for uid in uids if old.get(uid)}
        if len(prior_ids) > 1:
            raise IdentityReviewRequired(
                "MERGE_REQUIRED", {"component": component, "employer_ids": sorted(prior_ids)}
            )
        for employer in prior_ids:
            prior_components.setdefault(employer, set()).add(component)
    for employer, roots in prior_components.items():
        if len(roots) > 1:
            raise IdentityReviewRequired(
                "SPLIT_REQUIRED", {"employer_id": employer, "components": sorted(roots)}
            )
    absent_identities = (set(old.values()) - {None}) - set(prior_components)
    new_components = [
        root for root, uids in components.items() if not any(old.get(uid) for uid in uids)
    ]
    if absent_identities and new_components:
        # With source-key mapping alone, an unmatched new source could replace
        # a wholly absent old identity. Refuse to guess across a retraction.
        raise IdentityReviewRequired(
            "RETRACTION_REPLACEMENT_REVIEW_REQUIRED",
            {
                "absent_identity_count": len(absent_identities),
                "absent_identity_examples": sorted(absent_identities)[:20],
                "new_component_count": len(new_components),
            },
        )
    current = {uid: None for uid in rows}
    occupied = set(old.values()) - {None}
    for component, uids in sorted(components.items()):
        prior_ids = {old[uid] for uid in uids if old.get(uid)}
        employer = next(iter(prior_ids)) if prior_ids else "emp_" + min(uids)
        if not prior_ids and employer in occupied:
            raise IdentityReviewRequired("CANONICAL_ID_COLLISION", {"employer_id": employer})
        occupied.add(employer)
        for uid in uids:
            current[uid] = employer
    history = {**old, **current}
    if len(history) > MAX_RECORDS:
        raise ValueError(
            "registry history exceeds bounded source limit; explicit migration review required"
        )
    payload = {
        "namespace": NAMESPACE,
        "input_records_sha256": input_metadata["records_sha256"],
        "input_occurrences_sha256": input_metadata["occurrences_sha256"],
        "previous_registry_sha256": previous_digest,
        "assignments": [
            {"unique_id": uid, "employer_id": employer, "active": uid in rows}
            for uid, employer in sorted(history.items())
        ],
    }
    envelope = {"version": 1, "payload": payload, "content_sha256": content_hash(payload)}
    validate_registry(envelope)
    return current, envelope


def registry_metadata(path, previous=None):
    envelope = load_registry(path)
    if (digest(previous) if previous else None) != envelope["payload"]["previous_registry_sha256"]:
        raise ValueError("previous registry changed before publication")
    return {
        "registry_sha256": digest(path),
        "registry_content_sha256": envelope["content_sha256"],
        "registry_previous_sha256": envelope["payload"]["previous_registry_sha256"],
        "registry_previous_path": str(Path(previous).resolve()) if previous else None,
        "registry_mode": "INHERIT" if previous else "INITIALIZE",
    }


def rebuild_registry(
    records, membership, input_metadata, prediction, prediction_dir, previous_override=None
):
    path = Path(prediction_dir) / "registry.json"
    if digest(path) != prediction["registry_sha256"]:
        raise ValueError("published registry artifact digest mismatch")
    published = load_registry(path)
    if prediction["registry_content_sha256"] != published["content_sha256"]:
        raise ValueError("published registry content provenance mismatch")
    if prediction["registry_mode"] not in {"INITIALIZE", "INHERIT"}:
        raise ValueError("unknown registry publication mode")
    previous = previous_override or prediction.get("registry_previous_path")
    expected = prediction["registry_previous_sha256"]
    if (digest(previous) if previous else None) != expected:
        raise ValueError("previous registry provenance mismatch")
    if published["payload"]["previous_registry_sha256"] != expected:
        raise ValueError("published registry parent provenance mismatch")
    initialize = prediction["registry_mode"] == "INITIALIZE"
    canonical, envelope = resolve_registry(
        records, membership, input_metadata, initialize=initialize, previous=previous
    )
    return canonical, envelope, published
