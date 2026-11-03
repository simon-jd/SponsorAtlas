"""Version 2 fictional CDC fixture. Never opens existing or upstream databases."""

from pathlib import Path

import duckdb


def create_fixture(path: Path):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as db:
        db.execute("SET memory_limit='512MB'")
        db.execute("CREATE SCHEMA RAW_JOBS")
        db.execute("""CREATE TABLE RAW_JOBS.CDC_APPLICATION_EVENTS (
            id varchar, job_id varchar, event_type varchar, event_at timestamp,
            application_id varchar, owner_role varchar, contact_email varchar, contract_version integer,
            _ab_cdc_lsn bigint, _ab_cdc_updated_at timestamp, _ab_cdc_deleted_at timestamp,
            _airbyte_extracted_at timestamp)""")
        rows = [
            (
                "synthetic-e1",
                "synthetic-job-1",
                "submitted",
                "2026-01-01",
                "synthetic-app-1",
                "SPONSORATLAS_OWNER_SYNTHETIC",
                "first@example.invalid",
                2,
                1,
            ),
            (
                "synthetic-e2",
                "synthetic-job-1",
                "interview",
                "2026-01-02",
                "synthetic-app-1",
                "SPONSORATLAS_OWNER_SYNTHETIC",
                "recruiter@example.invalid",
                2,
                2,
            ),
            (
                "synthetic-e3",
                "synthetic-job-1",
                "submitted",
                "2026-01-03",
                "synthetic-app-2",
                "SPONSORATLAS_OWNER_OTHER",
                None,
                2,
                3,
            ),
            (
                "synthetic-e4",
                "synthetic-job-2",
                "submitted",
                "2026-01-01",
                "synthetic-app-3",
                "SPONSORATLAS_OWNER_SYNTHETIC",
                None,
                2,
                4,
            ),
            (
                "synthetic-e5",
                "synthetic-job-2",
                "application_deleted",
                "2026-01-04",
                "synthetic-app-3",
                "SPONSORATLAS_OWNER_SYNTHETIC",
                None,
                2,
                5,
            ),
            (
                "synthetic-e6",
                "synthetic-job-3",
                "submitted",
                "2026-01-01",
                "synthetic-app-4",
                "SPONSORATLAS_OWNER_SYNTHETIC",
                None,
                2,
                6,
            ),
        ]
        db.executemany(
            "INSERT INTO RAW_JOBS.CDC_APPLICATION_EVENTS VALUES (?,?,?,?,?,?,?,?,?,timestamp '2026-01-04',null,timestamp '2026-01-04')",
            rows,
        )
        db.execute(
            "UPDATE RAW_JOBS.CDC_APPLICATION_EVENTS SET _ab_cdc_deleted_at=timestamp '2026-01-05' WHERE id='synthetic-e6'"
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    create_fixture(parser.parse_args().path)
