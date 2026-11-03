"""Pure source-record schema and frozen annotation byte encoding."""

import hashlib
import json

SOURCE_FIELDS = (
    "unique_id",
    "source_system",
    "source_employer_key",
    "source_name",
    "state",
    "country",
    "city",
    "postcode",
    "tax_id",
    "strong_id",
    "issues",
    "eligible",
)
FULL_RECORD_FIELDS = (*SOURCE_FIELDS, "name_norm", "first_token", "tax_last4")
RECORD_SCHEMA = {key: "bool" if key == "eligible" else "string" for key in FULL_RECORD_FIELDS}


def annotation_record_hash(record):
    """Frozen annotation encoding, independently distinct from registry JSON.

    The generator is not retained; this exact encoding was corroborated against
    all 1200 trusted frozen snapshots using the pinned original normalizer.
    Acceptance still hashes actual FULL15-column input records, never snapshots.
    """
    raw = json.dumps(
        record, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
