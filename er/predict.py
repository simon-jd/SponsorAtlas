"""Splink probabilities plus conservative, deterministic constrained clustering."""

import argparse
import json
import math
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq
from splink import DuckDBAPI, Linker

from er.io import connect, digest, dump, quote, run_dir
from er.lineage import validate_input
from er.registry import (
    IdentityReviewRequired,
    registry_metadata,
    registry_request,
    resolve_registry,
    save_registry,
)
from er.settings import MAX_RECORDS, THRESHOLD
from er.train import candidate_upper_bound, load_input


def constrained_clusters(records, edges, threshold=THRESHOLD):
    rows = {r["unique_id"]: r for r in records}
    parent = {i: i for i in rows}
    strong = {i: {r["strong_id"]} if r.get("strong_id") else set() for i, r in rows.items()}
    countries = {i: {r["country"]} if r.get("country") else set() for i, r in rows.items()}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    decisions = []
    normalized = []
    for left, right, p in edges:
        if left not in rows or right not in rows:
            raise ValueError("edge endpoint absent from records")
        if left == right or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid pair prediction")
        left, right = sorted((left, right))
        normalized.append((left, right, float(p)))
    # Fixed total order prevents ambiguous bridge membership changing on iteration order.
    for left, right, p in sorted(set(normalized), key=lambda x: (-x[2], x[0], x[1])):
        l, r = rows[left], rows[right]
        a, b = find(left), find(right)
        exact = bool(l.get("strong_id") and l["strong_id"] == r.get("strong_id"))
        # LCA EMPLOYER_COUNTRY is free text ("GEORGIA", "NAMIBIA" next to a US EIN and ZIP).
        # A shared US EIN at the same postcode outweighs it; the cluster then stays US.
        country_typo = exact and bool(l.get("postcode")) and l["postcode"] == r.get("postcode")
        # Identical normalized name plus identical tax-ID suffix stands in for location
        # (another office, or a USCIS row without an address).
        name_and_suffix = bool(
            l.get("name_norm")
            and l["name_norm"] == r.get("name_norm")
            and l.get("tax_last4")
            and l["tax_last4"] == r.get("tax_last4")
        )
        reason = "ACCEPTED"
        kind = "DETERMINISTIC_US_EIN" if exact else "SPLINK"
        if not l.get("eligible") or not r.get("eligible"):
            reason = "UNRESOLVED_INPUT"
        elif len(strong[a] | strong[b]) > 1:
            reason = "STRONG_ID_CONFLICT"
        elif len(countries[a] | countries[b]) > 1 and not country_typo:
            reason = "COUNTRY_CONFLICT"
        elif (
            not exact
            and l.get("tax_last4")
            and r.get("tax_last4")
            and l["tax_last4"] != r["tax_last4"]
        ):
            reason = "TAX_SUFFIX_CONFLICT"
        elif (
            not exact
            and not name_and_suffix
            and (
                not l.get("state")
                or l["state"] != r.get("state")
                or not (
                    (l.get("city") and l["city"] == r.get("city"))
                    or (l.get("postcode") and l["postcode"] == r.get("postcode"))
                )
            )
        ):
            reason = "INSUFFICIENT_CONTEXT"
        elif not exact and p < threshold:
            reason = "BELOW_THRESHOLD"
        accepted = reason == "ACCEPTED"
        if accepted and a != b:
            low, high = sorted((a, b))
            parent[high] = low
            strong[low] |= strong[high]
            merged = countries[low] | countries[high]
            countries[low] = merged & {"US"} if country_typo and len(merged) > 1 else merged
        decisions.append(
            {
                "left_id": left,
                "right_id": right,
                "match_probability": p,
                "accepted": accepted,
                "reason": reason,
                "match_method": kind,
            }
        )
    return {i: find(i) for i in rows}, decisions


def build_xref(rows, canonical_ids, decisions):
    xref = []
    support = {i: [] for i in canonical_ids}
    for d in decisions:
        if d["accepted"]:
            for i in (d["left_id"], d["right_id"]):
                support[i].append(d)
    for r in rows:
        links = support[r["unique_id"]]
        xref.append(
            {
                "source_system": r["source_system"],
                "source_name": r["source_name"],
                "employer_id": canonical_ids[r["unique_id"]],
                "match_probability": max((d["match_probability"] for d in links), default=None),
                "source_employer_key": r["source_employer_key"],
                "state": r["state"],
                "country": r["country"],
                "city": r["city"],
                "postcode": r["postcode"],
                "unique_id": r["unique_id"],
                "issues": r["issues"],
                "resolution_status": (
                    "LINKED" if links else "REVIEW_MISSING_STATE" if not r["state"] else "SINGLETON"
                )
                if r["eligible"]
                else "UNRESOLVED",
                "match_method": (
                    "DETERMINISTIC_US_EIN"
                    if any(d["match_method"] == "DETERMINISTIC_US_EIN" for d in links)
                    else "SPLINK"
                )
                if links
                else None,
            }
        )
    return xref


def predict(input_dir, model_dir, output, *, initialize_registry=False, previous_registry=None):
    previous_digest = registry_request(initialize_registry, previous_registry)
    inp = run_dir(input_dir)
    model = run_dir(model_dir)
    manifest = json.loads((model / "training.json").read_text())
    input_metadata = validate_input(inp, training=manifest)
    if digest(model / "model.json") != manifest["model_sha256"]:
        raise ValueError("model digest mismatch")
    if pq.ParquetFile(inp / "records.parquet").metadata.num_rows > MAX_RECORDS:
        raise ValueError("bounded driver record limit exceeded")
    rows = pq.read_table(inp / "records.parquet").to_pylist()
    out = run_dir(output, create=True)
    c = connect(out / "analytics.duckdb")
    try:
        load_input(c, inp / "records.parquet")
        upper = candidate_upper_bound(c)
        db = DuckDBAPI(c)
        data = db.register(c.table("input_records"), dataset_display_name="employer_prediction")
        linker = Linker(data, str(model / "model.json"))
        pred = linker.inference.predict(threshold_match_probability=0.0)
        scored = c.sql(f"""SELECT unique_id_l AS left_id, unique_id_r AS right_id, match_probability
                           FROM {pred.physical_name} ORDER BY left_id,right_id""").to_arrow_table()
        pq.write_table(scored, out / "scores.parquet", compression="zstd")
        edges = [(r["left_id"], r["right_id"], r["match_probability"]) for r in scored.to_pylist()]
        membership, decisions = constrained_clusters(rows, edges)
        if previous_registry and digest(previous_registry) != previous_digest:
            raise ValueError("previous registry changed during prediction")
        canonical_ids, registry = resolve_registry(
            rows,
            membership,
            input_metadata,
            initialize=initialize_registry,
            previous=previous_registry,
        )
        save_registry(out / "registry.json", registry)
        identity_metadata = registry_metadata(out / "registry.json", previous_registry)
        xref = build_xref(rows, canonical_ids, decisions)
        pq.write_table(
            pa.Table.from_pylist(xref), out / "int_employer_xref.parquet", compression="zstd"
        )
        decision_schema = pa.schema(
            [
                ("left_id", pa.string()),
                ("right_id", pa.string()),
                ("match_probability", pa.float64()),
                ("accepted", pa.bool_()),
                ("reason", pa.string()),
                ("match_method", pa.string()),
            ]
        )
        pq.write_table(
            pa.Table.from_pylist(decisions, schema=decision_schema),
            out / "pair_decisions.parquet",
            compression="zstd",
        )
        c.execute("CREATE SCHEMA intermediate")
        validate_input(inp, training=manifest)
        c.execute(
            f"""CREATE TABLE intermediate.int_employer_xref AS SELECT * FROM read_parquet({quote(out / "int_employer_xref.parquet")})"""
        )
        c.execute(
            f"""CREATE TABLE intermediate.employer_source_occurrences AS SELECT * FROM read_parquet({quote(inp / "occurrences.parquet")})"""
        )
        # A non-null source key MUST be unique; source_name is never a join key.
        dup = c.execute("""SELECT count(*) FROM (SELECT source_system,source_employer_key FROM intermediate.int_employer_xref
                        WHERE source_employer_key IS NOT NULL GROUP BY ALL HAVING count(*)>1)""").fetchall()[
            0
        ][0]
        if dup:
            raise ValueError("xref source key fanout")
        summary = {
            "kind": "splink-duckdb-predict",
            "input_kind": manifest["input_kind"],
            "threshold": THRESHOLD,
            "source_records": len(rows),
            "candidates": scored.num_rows,
            "candidate_upper_bound": upper,
            "statuses": dict(Counter(r["resolution_status"] for r in xref)),
            "decisions": dict(Counter(d["reason"] for d in decisions)),
            "employers": len({r["employer_id"] for r in xref if r["employer_id"]}),
            "input_records_sha256": input_metadata["records_sha256"],
            "input_occurrences_sha256": input_metadata["occurrences_sha256"],
            "model_sha256": digest(model / "model.json"),
            "scores_sha256": digest(out / "scores.parquet"),
            "xref_sha256": digest(out / "int_employer_xref.parquet"),
            "limits": dict(
                c.execute(
                    "SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads','max_temp_directory_size','temp_directory')"
                ).fetchall()
            ),
            "acceptance_status": "UNVALIDATED_HUMAN_LABELS_REQUIRED",
            **identity_metadata,
        }
        dump(out / "prediction.json", summary)
        return summary
    finally:
        c.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--output", required=True)
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--initialize-registry", action="store_true")
    identity.add_argument("--previous-registry")
    a = p.parse_args()
    try:
        result = predict(
            a.input,
            a.model,
            a.output,
            initialize_registry=a.initialize_registry,
            previous_registry=a.previous_registry,
        )
    except IdentityReviewRequired as exc:
        print(json.dumps({"status": exc.status, "error": str(exc), "details": exc.details}))
        raise SystemExit(3) from exc
    print(json.dumps(result))


if __name__ == "__main__":
    main()
