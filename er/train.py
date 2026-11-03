"""Real bounded DuckDB Splink 5 u/EM training. No fabricated labels."""

import argparse
import csv
import json
import time

import splink
from splink import DuckDBAPI, Linker

from er.io import connect, digest, dump, quote, run_dir
from er.lineage import validate_input
from er.settings import (
    ID_BLOCK,
    MAX_CANDIDATES,
    MAX_RECORDS,
    MAX_U_PAIRS,
    NAME_BLOCK,
    SEED,
    settings,
)


def candidate_upper_bound(c, table="input_records"):
    total = 0
    for columns, condition in [
        ("first_token,state", "first_token IS NOT NULL AND state IS NOT NULL"),
        ("strong_id", "strong_id IS NOT NULL"),
        ("name_norm,tax_last4", "name_norm IS NOT NULL AND tax_last4 IS NOT NULL"),
    ]:
        total += c.execute(f"""SELECT coalesce(sum(n*(n-1)/2),0) FROM
          (SELECT count(*) n FROM {table} WHERE {condition} GROUP BY {columns})""").fetchall()[0][0]
    if total < 1:
        raise ValueError("no valid blocking pairs")
    if total > MAX_CANDIDATES:
        raise ValueError(
            f"candidate upper bound {total} exceeds {MAX_CANDIDATES}; refine source/blocking explicitly"
        )
    return int(total)


def load_input(c, records, excluded=()):
    c.execute(f"""CREATE OR REPLACE TABLE input_records AS SELECT * REPLACE
      (cast(strong_id AS VARCHAR) AS strong_id, cast(tax_last4 AS VARCHAR) AS tax_last4,
       cast(city AS VARCHAR) AS city, cast(postcode AS VARCHAR) AS postcode,
       cast(state AS VARCHAR) AS state) FROM read_parquet({quote(records)}) WHERE eligible""")
    c.execute("CREATE OR REPLACE TABLE excluded (unique_id VARCHAR)")
    if excluded:
        c.executemany("INSERT INTO excluded VALUES (?)", [(x,) for x in sorted(excluded)])
    c.execute("DELETE FROM input_records WHERE unique_id IN (SELECT unique_id FROM excluded)")
    n = c.execute("SELECT count(*) FROM input_records").fetchall()[0][0]
    if n < 2:
        raise ValueError("training/prediction requires at least two eligible records")
    if n > MAX_RECORDS:
        raise ValueError("bounded driver record limit exceeded")
    return n


def train(input_dir, output, labels=None, excluded_ids=()):
    """`excluded_ids` drops records (e.g. agent-label holdout endpoints) from u/EM."""
    inp = run_dir(input_dir)
    input_meta = validate_input(inp)
    out = run_dir(output, create=True)
    excluded = set(excluded_ids)
    label_hash = None
    if labels:
        with open(labels, newline="") as f:
            rows = list(csv.DictReader(f))
        if rows:
            from er.evaluate import validate_labels

            validate_labels(rows)
            excluded |= {
                r[k] for r in rows if r["split"] == "holdout" for k in ("left_id", "right_id")
            }
            label_hash = digest(labels)
    c = connect(out / "work.duckdb")
    started = time.monotonic()
    try:
        n = load_input(c, inp / "records.parquet", excluded)
        upper = candidate_upper_bound(c)
        ids = [
            r[0]
            for r in c.execute("SELECT unique_id FROM input_records ORDER BY unique_id").fetchall()
        ]
        (out / "training_ids.txt").write_text("\n".join(ids) + "\n")
        db = DuckDBAPI(c)
        data = db.register(c.table("input_records"), dataset_display_name="employer_training")
        linker = Linker(data, settings())
        linker.training.estimate_u_using_random_sampling(max_pairs=MAX_U_PAIRS, seed=SEED)
        sessions = []
        for rule in (NAME_BLOCK, ID_BLOCK):
            count = c.execute(
                f"SELECT count(*) FROM input_records l JOIN input_records r ON l.unique_id < r.unique_id AND ({rule})"
            ).fetchall()[0][0]
            if count:
                linker.training.estimate_parameters_using_expectation_maximisation(rule)
                sessions.append({"rule": rule, "pairs": count})
        if not sessions:
            raise ValueError("no EM sessions trained")
        linker.misc.save_model_to_json(str(out / "model.json"), overwrite=False)
        saved = json.loads((out / "model.json").read_text())
        untrained = [
            {
                "comparison": comp["output_column_name"],
                "level": level["label_for_charts"],
                "parameter": parameter,
            }
            for comp in saved["comparisons"]
            for level in comp["comparison_levels"]
            if not level.get("is_null_level")
            for parameter in ("m_probability", "u_probability")
            if parameter not in level
        ]
        manifest = {
            "kind": "splink-duckdb-u-em",
            "input_kind": input_meta["kind"],
            "splink_version": splink.__version__,
            "seed": SEED,
            "records": n,
            "excluded_holdout_records": len(excluded),
            "labels_sha256": label_hash,
            "acceptance_eligible": bool(label_hash) and not untrained,
            "untrained_parameters": untrained,
            "input_records_sha256": input_meta["records_sha256"],
            "input_occurrences_sha256": input_meta["occurrences_sha256"],
            "model_sha256": digest(out / "model.json"),
            "training_ids_sha256": digest(out / "training_ids.txt"),
            "candidate_upper_bound": upper,
            "max_u_pairs": MAX_U_PAIRS,
            "em_sessions": sessions,
            "limits": dict(
                c.execute(
                    "SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads','max_temp_directory_size','temp_directory')"
                ).fetchall()
            ),
            "seconds": round(time.monotonic() - started, 3),
        }
        validate_input(inp, training=manifest)
        dump(out / "training.json", manifest)
        return manifest
    finally:
        c.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--labels")
    a = p.parse_args()
    print(json.dumps(train(a.input, a.output, a.labels)))


if __name__ == "__main__":
    main()
