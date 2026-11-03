"""Strict frozen subagent label, source-evidence and revision validation.

This module consumes existing evidence only. It creates no labels or artifacts,
fits no model and supplies no human acceptance decision.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime

import pyarrow.parquet as pq

from er.evaluate import pair_key
from er.registry import content_hash
from er.source_record_codec import (
    FULL_RECORD_FIELDS as FULL_RECORD_FIELDS,  # noqa: PLC0414 -- retain public reexport
)
from er.source_record_codec import (
    RECORD_SCHEMA as RECORD_SCHEMA,  # noqa: PLC0414 -- retain public reexport
)
from er.source_record_codec import (
    SOURCE_FIELDS as SOURCE_FIELDS,  # noqa: PLC0414 -- retain public reexport
)
from er.source_record_codec import (
    annotation_record_hash as annotation_record_hash,  # noqa: PLC0414 -- retain public reexport
)

CANDIDATE_FIELDS = (
    "left_id",
    "right_id",
    "left_source_system",
    "left_source_employer_key",
    "right_source_system",
    "right_source_employer_key",
    "split",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(value):
    require(type(value) is str and bool(value.strip()), "reviewed_at metadata required")
    parsed = datetime.fromisoformat(value)
    require(
        parsed.tzinfo is not None and parsed.utcoffset() is not None,
        "reviewed_at requires timezone",
    )


def nonempty(value, name):
    require(type(value) is str and bool(value.strip()), f"missing reviewed metadata: {name}")


def decode_json(raw):
    # Duplicate JSON keys must not hide conflicting evidence.
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")),
    )


def label_counts(rows):
    resolved = [r for r in rows if r["label"] is not None]
    return {
        "reviewed": len(rows),
        "resolved": len(resolved),
        "positive": sum(r["label"] == 1 for r in resolved),
        "negative": sum(r["label"] == 0 for r in resolved),
        "abstained": len(rows) - len(resolved),
        "hard_cases": sum(r["hard_case"] for r in rows),
        "resolved_hard_cases": sum(r["hard_case"] for r in resolved),
    }


def validate_references(rows, candidates, frozen, manifest, validation):
    require(
        500 <= len(rows) <= 10_000 and len(rows) == len(candidates) == len(frozen),
        "reference count/coverage boundary mismatch",
    )
    seen, identities, splits = set(), set(), {}
    for index, (row, candidate, frozen_row) in enumerate(zip(rows, candidates, frozen), 1):
        require(
            row.get("origin") == row.get("label_method") == "subagent",
            "explicit subagent origin and label_method required",
        )
        require(
            type(row.get("label")) is int
            and row["label"] in (0, 1)
            or row.get("label") is None
            and "label" in row,
            "label must be exact integer0/1 or explicit null abstention",
        )
        require(
            type(row.get("hard_case")) is int and row["hard_case"] in (0, 1),
            "hard_case must be exact integer0/1",
        )
        require(
            type(row.get("pair_index")) is int and row["pair_index"] == index,
            "immutable pair order mismatch",
        )
        require(
            row.get("id") == f"sponsoratlas-agent-v1-{index:04d}" and row["id"] not in identities,
            "immutable label identity mismatch",
        )
        identities.add(row["id"])
        require(
            all(row.get(k) == candidate.get(k) for k in CANDIDATE_FIELDS)
            and all(row.get(k) == frozen_row.get(k) for k in ("left_id", "right_id", "split"))
            and all(row.get(k) == frozen_row[k] for k in CANDIDATE_FIELDS if k in frozen_row),
            "frozen candidate identity/split/source mismatch",
        )
        for key in (
            "left_id",
            "right_id",
            "reviewer",
            "review_model",
            "reasoning_effort",
            "confidence",
            "rationale",
            "label_disclosure",
            "left_source_system",
            "left_source_employer_key",
            "right_source_system",
            "right_source_employer_key",
        ):
            nonempty(row.get(key), key)
        stamp(row.get("reviewed_at"))
        require(row["split"] in ("train", "holdout"), "invalid frozen split")
        pair = pair_key(row["left_id"], row["right_id"])
        require(pair[0] != pair[1] and pair not in seen, "duplicate/self canonical pair")
        seen.add(pair)
        for uid in pair:
            require(
                uid not in splits or splits[uid] == row["split"], "endpoint crosses frozen split"
            )
            splits[uid] = row["split"]
        require(
            type(row.get("annotation_version")) is int and row["annotation_version"] == 2,
            "unsupported annotation provenance version",
        )
        provenance = row.get("provenance")
        require(
            isinstance(provenance, dict)
            and provenance.get("first_pass_artifact_sha256")
            == manifest.get("first_pass_agent_pairs_sha256")
            == validation.get("first_pass_artifact_sha256"),
            "first-pass provenance mismatch",
        )
        require(
            re.fullmatch("[a-f0-9]{64}", str(provenance.get("first_pass_artifact_sha256"))),
            "missing first-pass provenance hash",
        )
        stamp(provenance.get("first_pass_reviewed_at"))
        require(
            "first_pass_label" in provenance
            and (
                provenance.get("first_pass_label") is None
                or type(provenance.get("first_pass_label")) is int
                and provenance["first_pass_label"] in (0, 1)
            ),
            "invalid first-pass label provenance",
        )
        require(
            provenance.get("record_hash_definition")
            == "SHA256 of entire original record dict serialized as sorted-key compact JSON; unchanged across revisions.",
            "unsupported source record hash provenance",
        )
        evidence = row.get("evidence")
        require(isinstance(evidence, dict), "missing source evidence")
        stage = provenance.get("stage")
        require(
            stage
            in (
                "first_pass_blinded_source_assisted_subagent_review",
                "second_pass_external_source_review",
            ),
            "unknown review provenance stage",
        )
        if stage == "first_pass_blinded_source_assisted_subagent_review":
            require(
                provenance.get("first_pass_label") == row["label"],
                "first-pass label provenance changed without revision",
            )
            require(
                provenance["first_pass_reviewed_at"] == row["reviewed_at"],
                "first-pass reviewed time provenance mismatch",
            )
        else:
            require(
                provenance.get("reviewer_independently_opened_primary_sources") is True
                and provenance.get("external_input_artifact_sha256")
                == validation.get("external_input_artifact_sha256"),
                "second-pass external evidence provenance mismatch",
            )
            supplemental = evidence.get("supplemental_primary_sources")
            require(
                isinstance(supplemental, list) and supplemental,
                "second-pass primary evidence required",
            )
            for source in supplemental:
                for key in (
                    "url",
                    "title",
                    "locator",
                    "observed_fact",
                    "verification",
                    "limitation",
                ):
                    nonempty(source.get(key), f"primary evidence {key}")
                require(source["url"].startswith("https://"), "primary evidence URL required")
        for side in ("left", "right"):
            entry = evidence.get(side)
            require(
                isinstance(entry, dict)
                and isinstance(entry.get("source_record"), dict)
                and isinstance(entry.get("official_source_occurrences"), list)
                and entry["official_source_occurrences"],
                "missing endpoint source evidence",
            )
    counts = label_counts(rows)
    for key, count in counts.items():
        require(
            type(manifest.get(key)) is int
            and manifest[key] == count
            and type(validation.get(key)) is int
            and validation[key] == count,
            f"label count manifest mismatch: {key}",
        )
    require(
        counts["resolved"] >= 500 and counts["resolved_hard_cases"] >= 100,
        "requires500 resolved pairs/100 resolved hard cases",
    )
    holdout = [r for r in rows if r["split"] == "holdout" and r["label"] is not None]
    require(
        {r["label"] for r in holdout} == {0, 1} and any(r["hard_case"] == 1 for r in holdout),
        "resolved holdout needs positive/negative/hard examples",
    )
    require(
        any(r["split"] == "train" and r["label"] is not None for r in rows),
        "nonempty resolved development train required",
    )
    return counts


def verify_revisions(rows, manifest, validation, annotation_dir, observed):
    revised = {
        r["pair_index"]: r
        for r in rows
        if r["provenance"]["stage"] == "second_pass_external_source_review"
    }
    if not revised:
        require(
            not manifest.get("changed_pairs") and not validation.get("changed_pairs"),
            "revision provenance count mismatch",
        )
        return
    for metadata in (manifest, validation):
        changed = metadata.get("changed_pairs")
        require(
            isinstance(changed, list)
            and all(type(i) is int for i in changed)
            and changed == sorted(revised),
            "revision provenance changed-pair mismatch",
        )
    external = annotation_dir / "external-evidence-input-snapshot.jsonl"
    require(
        observed.get(external) == validation.get("external_input_artifact_sha256")
        and external in observed,
        "external evidence provenance digest mismatch",
    )

    def indexed(name):
        path = annotation_dir / name
        require(path in observed, "missing frozen primary/revision provenance artifact")
        entries = [decode_json(line) for line in path.read_text().splitlines()]
        require(
            all(isinstance(r, dict) and type(r.get("pair_index")) is int for r in entries),
            "invalid primary/revision provenance identity",
        )
        by_index = {r["pair_index"]: r for r in entries}
        require(
            len(by_index) == len(entries) and set(by_index) == set(revised),
            "primary/revision provenance coverage mismatch",
        )
        return by_index

    primary = indexed("independently-reviewed-primary-sources.jsonl")
    audit = indexed("revision-audit.jsonl")
    for index, row in revised.items():
        source, revision = primary[index], audit[index]
        evidence = row["evidence"]["supplemental_primary_sources"]
        require(
            source.get("origin") == "subagent"
            and source.get("reviewer") == row["reviewer"]
            and source.get("checked_at") == row["reviewed_at"]
            and source.get("sources") == evidence,
            "primary evidence provenance mismatch",
        )
        require(
            revision.get("origin") == revision.get("label_method") == "subagent"
            and revision.get("left_id") == row["left_id"]
            and revision.get("right_id") == row["right_id"]
            and revision.get("reviewer") == row["reviewer"]
            and revision.get("changed_at") == row["reviewed_at"]
            and revision.get("before", {}).get("label") == row["provenance"]["first_pass_label"]
            and revision.get("after")
            == {k: row[k] for k in ("label", "confidence", "rationale", "reviewed_at")}
            and revision.get("evidence") == evidence
            and revision.get("first_pass_artifact_sha256")
            == row["provenance"]["first_pass_artifact_sha256"]
            and revision.get("external_input_artifact_sha256") == observed[external],
            "revision evidence provenance mismatch",
        )


def verify_source_evidence(rows, records, occurrences_path, input_metadata):
    require(
        all(
            set(record) == set(RECORD_SCHEMA)
            and type(record["eligible"]) is bool
            and all(
                value is None or type(value) is str
                for key, value in record.items()
                if key != "eligible"
            )
            for record in records
        ),
        "full15-column actual record schema/type mismatch",
    )
    by_id = {r["unique_id"]: r for r in records}
    require(len(by_id) == len(records), "duplicate input record IDs")
    endpoints = {r[side + "_id"] for r in rows for side in ("left", "right")}
    require(endpoints <= set(by_id), "label endpoint absent from input")
    occurrences = defaultdict(list)
    file = pq.ParquetFile(occurrences_path)
    require(
        file.metadata.num_rows == input_metadata["rows"] <= 1_000_000,
        "occurrence count/coverage boundary mismatch",
    )
    original_counts = Counter()
    for batch in file.iter_batches(batch_size=4096, use_threads=False):
        for occurrence in batch.to_pylist():
            key = (
                occurrence["source_system"],
                occurrence["source_release"],
                occurrence["source_sha256"],
            )
            original_counts[key] += 1
            if occurrence["unique_id"] in endpoints:
                occurrences[occurrence["unique_id"]].append(occurrence)
    expected_originals = {
        (r["source_system"], r["source_release"], r["source_sha256"]): r["rows"]
        for r in input_metadata["originals"]
    }
    require(
        dict(original_counts) == expected_originals, "original release/SHA row lineage mismatch"
    )
    for row in rows:
        for side in ("left", "right"):
            uid = row[side + "_id"]
            actual = by_id[uid]
            entry = row["evidence"][side]
            source = entry["source_record"]
            require(
                all(
                    key in source
                    and type(source[key]) is type(actual.get(key))
                    and source[key] == actual.get(key)
                    for key in SOURCE_FIELDS
                ),
                "endpoint source record evidence mismatch",
            )
            require(
                all(
                    key == "record_sha256"
                    or key in actual
                    and type(value) is type(actual[key])
                    and value == actual[key]
                    for key, value in source.items()
                ),
                "endpoint source snapshot mismatch",
            )
            require(
                actual["source_system"] == row[side + "_source_system"]
                and actual["source_employer_key"] == row[side + "_source_employer_key"],
                "endpoint source-key provenance mismatch",
            )
            record_hash = annotation_record_hash(actual)
            require(
                isinstance(row.get("source_record_hashes"), dict)
                and row["source_record_hashes"].get(side)
                == source.get("record_sha256")
                == record_hash,
                "actual source record hash mismatch",
            )
            # Compare complete multisets, preserving repeated original source rows.
            claimed = entry["official_source_occurrences"]
            require(
                Counter(content_hash(o) for o in claimed)
                == Counter(content_hash(o) for o in occurrences[uid]),
                "actual official occurrence evidence mismatch",
            )
    return by_id
