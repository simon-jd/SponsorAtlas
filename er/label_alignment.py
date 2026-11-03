"""Map frozen label pairs onto an ER input and report the pairs that no longer match.

`unique_id` is sha256(source_system:source_employer_key), so an ID from an older input
is found again when the same source key is still exported. A pair does not match when
an endpoint is absent (for example a USCIS row that is no longer released) or when the
label row names a different source system/key than the record with that ID.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from er.io import dump, run_dir

COLUMNS = ["unique_id", "source_system", "source_employer_key", "eligible"]


def read_records(input_dir):
    return pq.read_table(Path(input_dir) / "records.parquet", columns=COLUMNS).to_pylist()


def read_pairs(path):
    csv.field_size_limit(16 * 1024 * 1024)  # agent CSVs embed full source evidence
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _side(pair, side, by_id):
    record = by_id.get(pair[f"{side}_id"])
    if record is None:
        return "ABSENT"
    for field in ("source_system", "source_employer_key"):
        expected = pair.get(f"{side}_{field}")
        if expected not in (None, "") and expected != record[field]:
            return "SOURCE_KEY_MISMATCH"
    return None


def align(pairs, records):
    by_id = {r["unique_id"]: r for r in records}
    unmatched = []
    for index, pair in enumerate(pairs):
        left, right = _side(pair, "left", by_id), _side(pair, "right", by_id)
        if left is None and right is None:
            continue
        if left == right == "ABSENT":
            reason = "BOTH_ABSENT"
        elif left:
            reason = f"LEFT_{left}"
        else:
            reason = f"RIGHT_{right}"
        unmatched.append(
            {
                "index": index,
                "pair_id": pair.get("pair_id") or pair.get("id"),
                "left_id": pair["left_id"],
                "right_id": pair["right_id"],
                "split": pair.get("split"),
                "reason": reason,
            }
        )
    return {
        "pairs": len(pairs),
        "matched": len(pairs) - len(unmatched),
        "unmatched_by_reason": dict(sorted(Counter(u["reason"] for u in unmatched).items())),
        "unmatched": unmatched,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="ER input dir with records.parquet")
    p.add_argument("--labels", required=True, help="CSV with left_id/right_id per pair")
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    result = align(read_pairs(a.labels), read_records(run_dir(a.input)))
    result["input"] = str(Path(a.input).resolve())
    result["labels"] = str(Path(a.labels).resolve())
    dump(a.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "unmatched"}, indent=1))
    raise SystemExit(0 if result["matched"] == result["pairs"] else 1)


if __name__ == "__main__":
    main()
