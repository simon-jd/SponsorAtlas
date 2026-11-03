"""Explicit synthetic HTTP transport fixture. This is not Airbyte or Slack."""

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orchestration.sponsoratlas_orchestration.runtime import ROOT
from scripts.canonical_paths import FACTS_DB


def prepare():
    from scripts.local_duckdb import connect_local
    from scripts.staging_fixture import create_fixture

    database = ROOT / ".tools/orchestration/synthetic.duckdb"
    create_fixture(database)
    protected = FACTS_DB
    with connect_local(database) as c:
        c.execute("attach '" + str(protected).replace("'", "''") + "' as reviewed (read_only)")
        c.execute("create schema DIMENSIONS_INPUT; create schema FACTS_INPUT")
        c.execute(
            "create table FACTS_INPUT.lca as select * from reviewed.FACTS_INPUT.lca order by case_number limit 2"
        )
        c.execute(
            "create table DIMENSIONS_INPUT.lca_attributes as select a.* from reviewed.DIMENSIONS_INPUT.lca_attributes a join FACTS_INPUT.lca l using(source_sha256,source_row_number)"
        )
        c.execute(
            "create table DIMENSIONS_INPUT.employer_sources as select * from reviewed.DIMENSIONS_INPUT.employer_sources"
        )
        c.execute(
            "create table FACTS_INPUT.job_observations as select * replace('SYNTHETIC_ORCHESTRATION_REPLAY' as replay_kind) from reviewed.FACTS_INPUT.job_observations order by job_key limit 2"
        )
        # Small, explicitly labelled integration scope; no claim of full-source quality or FY coverage.
        c.execute("delete from RAW_BLS.BLS_FY2024; delete from RAW_BLS.BLS_FY2025")
        c.execute(
            "insert into RAW_BLS.BLS_FY2025 select r.* from reviewed.RAW_BLS.BLS_FY2025 r join reviewed.STAGING.stg_bls b using(source_release,source_sha256,source_row_number) where b.industry_group='cross-industry' and b.ownership_code='1235' and b.naics='000000' and b.area_type='1' and b.area_code='99' and b.soc_code in ('00-0000','13-1020','15-1252','21-1018')"
        )
        for table in ["ACME_GH_GREENHOUSE", "ACME_LV_LEVER", "ACME_AB_ASHBY", "CDC_JOB_POSTINGS"]:
            # These are synthetic load timestamps only. Archived FACTS_INPUT observation days stay intact.
            c.execute(
                "update RAW_JOBS."
                + table
                + " set _airbyte_extracted_at=timezone('UTC', current_timestamp)"
            )
        observation_days = c.execute(
            "select distinct cast(observed_at_utc as varchar) from FACTS_INPUT.job_observations order by 1"
        ).fetchall()
    print(
        json.dumps(
            {
                "kind": "SYNTHETIC_ORCHESTRATION_INTEGRATION_ONLY",
                "database": str(database),
                "job_original_observations": observation_days,
            }
        )
    )


def serve(port):
    output = ROOT / ".tools/orchestration/http-events.jsonl"
    state = ROOT / ".tools/orchestration/synthetic-http-state.json"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            if length > 16384:
                self.send_error(413)
                return
            data = json.loads(self.rfile.read(length))
            if self.path == "/applications/token":
                response = {"access_token": "synthetic-local-token"}
            elif self.path == "/jobs":
                value = json.loads(state.read_text()) if state.exists() else {"triggers": 0}
                value["triggers"] += 1
                state.write_text(json.dumps(value))
                response = {"jobId": value["triggers"]}
                with output.open("a") as out:
                    out.write(
                        json.dumps(
                            {
                                "path": self.path,
                                "connection_id": data.get("connectionId"),
                                "job_id": value["triggers"],
                                "at_utc": datetime.now(UTC).isoformat(),
                            }
                        )
                        + "\n"
                    )
            elif self.path == "/alerts":
                with output.open("a") as out:
                    out.write(
                        json.dumps(
                            {"path": self.path, **data, "at_utc": datetime.now(UTC).isoformat()}
                        )
                        + "\n"
                    )
                response = {"received": True}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def do_GET(self):
            if not self.path.startswith("/jobs/"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "succeeded"}).encode())

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "serve"])
    parser.add_argument("--port", type=int, default=18789)
    args = parser.parse_args()
    prepare() if args.command == "prepare" else serve(args.port)
