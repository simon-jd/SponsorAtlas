"""Private Task10 profiling and bounded trial experiments (never auto-load .env).

Fixture mode cannot connect. Remote mode requires an explicit finite trial and JWT.
Only query ids, hashes, aggregate measurements and restoration state are published.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.snowflake_live import secondary_roles_disabled

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = "SPONSORATLAS_XS"
TAG = "SponsorAtlas_Task10"
MAX_STATEMENTS = 100
STATEMENT_SECONDS = 30
RUN_SECONDS = 300
CLEANUP_SECONDS = 120

# Exact warehouse AND exact application tag, bound timestamps; never read query_text.
HISTORY_SQL = """
SELECT q.query_id,q.query_hash,q.total_elapsed_time,q.execution_time,
 q.partitions_scanned,q.partitions_total,q.bytes_scanned,
 q.percentage_scanned_from_cache,q.bytes_read_from_result,
 q.credits_used_cloud_services,q.execution_status,q.start_time,q.end_time,
 a.query_id AS attribution_query_id,a.credits_attributed_compute,
 a.credits_used_query_acceleration
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY q
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a ON q.query_id=a.query_id
WHERE q.warehouse_name=%s AND q.query_tag=%s
 AND q.start_time >= %s AND q.start_time < %s
ORDER BY a.credits_attributed_compute DESC NULLS LAST,q.query_id
LIMIT 20
"""
METERING_SQL = """
SELECT start_time,end_time,credits_used_compute,credits_used_cloud_services,
 credits_attributed_compute_queries
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE warehouse_name=%s AND start_time < %s AND end_time > %s
ORDER BY start_time LIMIT 25
"""


def number(value, *, integer=False):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean measurement")  # noqa: TRY004 -- one input-validation contract
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Non-numeric measurement") from None
    if not math.isfinite(result) or result < 0 or (integer and result != int(result)):
        raise ValueError("Invalid nonnegative finite measurement")
    return int(result) if integer else result


def upper(row):
    return {k.upper(): v for k, v in row.items()}


def build_report(history, attribution, *, synthetic=False, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError("Bounded report limit required")
    indexed = {}
    for raw in attribution:
        row = upper(raw)
        key = str(row["QUERY_ID"])
        if key in indexed:
            raise ValueError("Duplicate attribution query_id")
        indexed[key] = row
    rows, seen = [], set()
    for raw in history:
        q = upper(raw)
        key = str(q["QUERY_ID"])
        if key in seen or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise ValueError("Invalid or duplicate query_id")
        seen.add(key)
        a = indexed.get(key, {})
        credits = number(a.get("CREDITS_ATTRIBUTED_COMPUTE"))
        item = {"query_id": key, "query_hash": q.get("QUERY_HASH"),
                "execution_status": q.get("EXECUTION_STATUS"),
                "credits_attributed_compute": credits,
                "credits_used_query_acceleration": number(a.get("CREDITS_USED_QUERY_ACCELERATION")),
                "attribution_status": "AVAILABLE" if credits is not None else
                "MISSING_DELAY_OR_SHORT_QUERY"}
        for source, target, integer in [
            ("TOTAL_ELAPSED_TIME", "elapsed_ms", True),
            ("EXECUTION_TIME", "execution_ms", True),
            ("PARTITIONS_SCANNED", "partitions_scanned", True),
            ("PARTITIONS_TOTAL", "partitions_total", True),
            ("BYTES_SCANNED", "bytes_scanned", True),
            ("BYTES_READ_FROM_RESULT", "bytes_read_from_result", True),
            ("PERCENTAGE_SCANNED_FROM_CACHE", "warehouse_cache_fraction", False),
            ("CREDITS_USED_CLOUD_SERVICES", "cloud_services_unadjusted_credits", False),
        ]:
            item[target] = number(q.get(source), integer=integer)
        fraction = item["warehouse_cache_fraction"]
        if fraction is not None and fraction > 1:
            raise ValueError("Invalid warehouse cache fraction")
        rows.append(item)
    rows.sort(key=lambda r: (r["credits_attributed_compute"] is None,
                            -(r["credits_attributed_compute"] or 0), r["query_id"]))
    return {"kind": "SYNTHETIC_QUERY_HISTORY" if synthetic else "ACTUAL_SNOWFLAKE_QUERY_HISTORY",
            "rank_by": "credits_attributed_compute DESC NULLS LAST, query_id",
            "queries": rows[:limit], "input_queries": len(rows),
            "missing_attribution": sum(r["credits_attributed_compute"] is None for r in rows),
            "cost_scope": "query compute only; idle, cloud services, storage and clustering separate",
            "attribution_latency_hours": 8,
            "result_cache_hit": "UNKNOWN: missing attribution or fast execution is not proof"}


def utc(value):
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise ValueError("Explicit UTC timestamp required")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def admission(env=None, *, now=None, experiment=False):
    env = os.environ if env is None else env
    if env.get("SPONSORATLAS_COST_MODE") != "production":
        raise ValueError("Explicit production cost mode required")
    if env.get("SPONSORATLAS_COST_OPT_IN") != "YES":
        raise ValueError("Explicit Snowflake cost opt-in required")
    start, end = (utc(env.get(k, "")) for k in
                  ("SNOWFLAKE_TRIAL_STARTED_AT", "SNOWFLAKE_TRIAL_EXPIRES_AT"))
    now = now or datetime.now(UTC)
    if not 0 < (end - start).total_seconds() <= 30 * 86400 or not start <= now:
        raise ValueError("Finite active trial of at most 30 days required")
    if (end - now).total_seconds() <= 420 + RUN_SECONDS:
        raise ValueError("Insufficient trial window including cleanup reserve")
    balance = number(env.get("SPONSORATLAS_TRIAL_REMAINING_CREDITS"))
    budget = number(env.get("SPONSORATLAS_COST_MAX_CREDITS"))
    if balance is None or budget is None or not 0 < budget <= balance:
        raise ValueError("Explicit positive trial balance and run credit allowance required")
    user = "COST_EXPERIMENT_SVC" if experiment else "COST_OBSERVER_SVC"
    role = "COST_EXPERIMENTER" if experiment else "COST_OBSERVER"
    for key, value in {"SNOWFLAKE_COST_USER": user, "SNOWFLAKE_COST_ROLE": role}.items():
        if env.get(key) != value:
            raise ValueError("Dedicated minimal cost identity required")
    if env.get("SNOWFLAKE_PASSWORD"):
        raise ValueError("Password authentication forbidden")
    for key in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_COST_PRIVATE_KEY_FILE"):
        if not env.get(key):
            raise ValueError("Missing cost key-pair connection configuration")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", env["SNOWFLAKE_ACCOUNT"]):
        raise ValueError("Invalid Snowflake account")
    key = Path(env["SNOWFLAKE_COST_PRIVATE_KEY_FILE"])
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise ValueError("Owner-only cost RSA key required")
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    passphrase = env.get("SNOWFLAKE_COST_PRIVATE_KEY_PASSPHRASE")
    private = load_pem_private_key(key.read_bytes(), password=passphrase.encode() if passphrase else None)
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

    if not isinstance(private, RSAPrivateKey) or private.key_size < 2048:
        raise ValueError("RSA 2048+ cost key required")
    if experiment and env.get("SPONSORATLAS_COST_EXPERIMENT_OPT_IN") != "YES":
        raise ValueError("Explicit mutable trial experiment opt-in required")
    if experiment and env.get("SPONSORATLAS_WAREHOUSE_EXCLUSIVE") != "YES":
        raise ValueError("Explicit exclusive project warehouse maintenance window required")
    return {"account": env["SNOWFLAKE_ACCOUNT"], "user": user, "role": role,
            "warehouse": WAREHOUSE, "database": "ANALYTICS", "schema": "MARTS",
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": env["SNOWFLAKE_COST_PRIVATE_KEY_FILE"],
            "private_key_file_pwd": env.get("SNOWFLAKE_COST_PRIVATE_KEY_PASSPHRASE"),
            "login_timeout": 10, "network_timeout": 10, "socket_timeout": 10,
            "backoff_policy": lambda: iter([1, 1]), "client_session_keep_alive": False,
            "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_SECONDS,
                                   "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 10,
                                   "ABORT_DETACHED_QUERY": True, "QUERY_TAG": TAG}}


class _BudgetInterrupt(BaseException):
    """Unwind connector retries as well as socket/fetch/cancellation waits."""


def bounded_call(callback, deadline, clock=time.monotonic):
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("Absolute cost deadline exhausted")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Bounded Snowflake controller requires the main thread")
    handler = signal.getsignal(signal.SIGALRM)
    previous, interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(signum, frame):
        raise _BudgetInterrupt()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(remaining, previous) if previous else remaining)
    try:
        value = callback()
        if clock() >= deadline:
            raise TimeoutError("Late Snowflake response rejected")
        return value
    except _BudgetInterrupt:
        raise TimeoutError("Absolute cost deadline interrupted a blocking operation") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if previous:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, previous - (time.monotonic() - started)), interval)


class BoundedSession:
    """Server timeout plus absolute client deadline; late results never become evidence.

    On an ambiguous transport failure the caller must perform explicit recovery; it
    cannot infer that a DDL failed. MutationSession journals our exact generated
    maintenance intent, session and provider id, never account query_text.
    """

    def __init__(self, connection, *, seconds=RUN_SECONDS, cleanup_seconds=CLEANUP_SECONDS,
                 trial_deadline=None, started=None, clock=time.monotonic):
        self.connection, self.clock = connection, clock
        started = clock() if started is None else started
        self.cleanup_deadline = min(started + seconds + cleanup_seconds,
                                    trial_deadline if trial_deadline is not None else started + seconds + cleanup_seconds)
        self.deadline = min(started + seconds, self.cleanup_deadline - cleanup_seconds)
        self.count = 0
        self.cleanup_count = 0
        self.query_ids = []
        self.session_id = getattr(connection, "session_id", None)
        self.query_observer = None

    def remaining(self, *, cleanup=False):
        deadline = self.cleanup_deadline if cleanup else self.deadline
        remaining = deadline - self.clock()
        if remaining < 1:
            raise TimeoutError("Absolute cost query budget exhausted")
        return deadline, min(STATEMENT_SECONDS, math.floor(remaining))

    def call(self, callback, *, cleanup=False):
        deadline, _ = self.remaining(cleanup=cleanup)
        return bounded_call(callback, deadline, self.clock)

    def query_status(self, query_id, *, cleanup=False):
        # SDK status HTTP and result parsing share the same absolute cleanup bound.
        if self.cleanup_count >= 16:
            raise TimeoutError("Absolute cost status budget exhausted")
        self.cleanup_count += 1
        return self.call(lambda: self.connection.get_query_status(query_id), cleanup=cleanup)

    def close(self):
        # No new connection/session-delete request can escape the cleanup cutoff.
        self.call(self.connection.close, cleanup=True)

    def run(self, sql, params=None, *, cleanup=False):
        if (not cleanup and (self.count >= MAX_STATEMENTS or self.clock() >= self.deadline)) or (
            cleanup and self.cleanup_count >= 16
        ):
            raise TimeoutError("Absolute cost query budget exhausted")
        if cleanup:
            self.cleanup_count += 1
        self.count += 1
        deadline, _ = self.remaining(cleanup=cleanup)
        cursor = self.call(self.connection.cursor, cleanup=cleanup)

        def observe():
            if cursor.sfqid:
                if cursor.sfqid not in self.query_ids:
                    self.query_ids.append(cursor.sfqid)
                if self.query_observer:
                    self.query_observer(cursor.sfqid)

        def execute():
            # Cursor creation may have spent time: tighten again at SQL dispatch.
            _, timeout = self.remaining(cleanup=cleanup)
            return cursor.execute(sql, params, timeout=timeout)

        try:
            bounded_call(execute, deadline, self.clock)
            observe()  # persist the provider id before any fetch can block or crash
            rows = self.call(cursor.fetchall, cleanup=cleanup) if cursor.description else []
            return [dict(zip([d[0].upper() for d in cursor.description], row)) for row in rows]
        except BaseException:
            observe()
            raise
        finally:
            # Closing after expiry cannot extend the run or imply remote cancellation.
            if self.clock() < deadline:
                bounded_call(cursor.close, deadline, self.clock)


TERMINAL_MUTATIONS = {"SUCCESS", "ABORTED", "FAILED_WITH_ERROR", "FAILED_WITH_INCIDENT"}


class MutationSession:
    """Write-ahead exact control statements; a lost provider ID stays unresolved.

    This journal contains only our fixed maintenance SQL, never account query_text.
    It is deliberately conservative across a crash between intent and dispatch.
    """

    def __init__(self, base, report, checkpoint):
        self.base, self.report, self.checkpoint = base, report, checkpoint
        if not str(getattr(base, "session_id", "")).isdigit() or int(base.session_id) <= 0:
            raise ValueError("Known originating Snowflake session required")

    def __getattr__(self, name):
        return getattr(self.base, name)

    def run(self, sql, params=None, *, cleanup=False):
        if not re.match(r"^(CREATE|ALTER|DROP)\b", sql, flags=re.IGNORECASE):
            return self.base.run(sql, params, cleanup=cleanup)
        if isinstance(self.base, BoundedSession):
            self.base.remaining(cleanup=cleanup)
        if params:
            raise ValueError("Mutation journal requires exact generated control SQL")
        intent = {"intent_id": uuid.uuid4().hex, "originating_session": int(self.base.session_id),
                  "statement": sql, "query_id": None, "state": "INTENT"}
        self.report["mutations"].append(intent)
        self.checkpoint(self.report)  # crash here is unresolved, not permission to retry
        previous_observer = getattr(self.base, "query_observer", None)
        previous_ids = len(self.base.query_ids)

        def observe(query_id):
            intent["query_id"] = query_id
            intent["state"] = "QUERY_ID_KNOWN"
            self.checkpoint(self.report)

        self.base.query_observer = observe
        try:
            result = self.base.run(sql, params, cleanup=cleanup)
            if len(self.base.query_ids) > previous_ids:
                intent["query_id"] = self.base.query_ids[-1]
            if not intent["query_id"]:
                raise RuntimeError("Mutation acknowledgement has no provider query ID")
            intent["state"] = "SUCCESS"  # synchronous execute/fetch completed within deadline
            self.checkpoint(self.report)
            return result
        except BaseException:
            if len(self.base.query_ids) > previous_ids:
                intent["query_id"] = self.base.query_ids[-1]
            intent["state"] = "UNKNOWN"
            self.checkpoint(self.report)
            raise
        finally:
            self.base.query_observer = previous_observer


def valid_session_id(value):
    return str(value).isdigit() and int(value) > 0


def mutation_journal_valid(report):
    return (isinstance(report, dict) and report.get("mutation_protocol") == 1
            and isinstance(report.get("mutations"), list)
            and valid_session_id(report.get("originating_session")))


def mutation_identity_valid(intent):
    return (valid_session_id(intent.get("originating_session"))
            and isinstance(intent.get("query_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", intent["query_id"]) is not None)


def journal_cleared(report):
    return (mutation_journal_valid(report)
            and report.get("restoration", {}).get("status") == "VERIFIED"
            and all(mutation_identity_valid(i) and i.get("state") in TERMINAL_MUTATIONS
                    for i in report["mutations"]))


def confirm_mutations(session, report, checkpoint):
    if not mutation_journal_valid(report):
        return ["MISSING_MUTATION_JOURNAL"]
    errors = []
    for intent in report["mutations"]:
        query_id = intent.get("query_id")
        if not mutation_identity_valid(intent):
            errors.append("MUTATION_ID_OR_ORIGIN_UNKNOWN")
            continue
        if intent.get("state") in TERMINAL_MUTATIONS:
            continue
        try:
            for attempt in range(3):
                status = session.query_status(query_id, cleanup=True)
                status = getattr(status, "name", status)
                if status in TERMINAL_MUTATIONS:
                    intent["state"] = status
                    checkpoint(report)
                    break
                if attempt == 0:
                    # A cancellation request acknowledgement is not completion.
                    intent["cancellation_requested"] = True
                    checkpoint(report)
                    session.run("SELECT SYSTEM$CANCEL_QUERY(%s)", (query_id,), cleanup=True)
            else:
                errors.append("MUTATION_NOT_TERMINAL")
        except Exception as error:  # noqa: BLE001 -- unknown/late status must keep the barrier
            errors.append(type(error).__name__)
    return errors


def connect(*, experiment=False):
    options = admission(experiment=experiment)
    import snowflake.connector

    started = time.monotonic()
    remaining_trial = (utc(os.environ["SNOWFLAKE_TRIAL_EXPIRES_AT"]) - datetime.now(UTC)).total_seconds() - 420
    total_deadline = min(started + RUN_SECONDS + CLEANUP_SECONDS, started + remaining_trial)
    work_deadline = min(started + RUN_SECONDS, total_deadline - CLEANUP_SECONDS)
    remaining = math.floor(work_deadline - time.monotonic())
    if remaining < 1:
        raise TimeoutError("No bounded connection window remains")
    for option in ("login_timeout", "network_timeout", "socket_timeout"):
        options[option] = min(options[option], remaining)
    connection = bounded_call(lambda: snowflake.connector.connect(**options), work_deadline)
    session = BoundedSession(connection, started=started, trial_deadline=total_deadline)
    try:
        session.run("USE SECONDARY ROLES NONE")
        rows = session.run("SELECT CURRENT_USER() AS U,CURRENT_ROLE() AS R,"
                           "CURRENT_WAREHOUSE() AS W,CURRENT_SECONDARY_ROLES() AS S,CURRENT_SESSION() AS SID")
        row = rows[0]
        if (row["U"], row["R"], row["W"]) != (options["user"], options["role"], WAREHOUSE):
            raise ValueError("Unexpected cost session identity")
        if not secondary_roles_disabled(row["S"]):
            raise ValueError("Secondary roles must be NONE")
        session.session_id = int(row["SID"])
        return session
    except BaseException:
        session.close()
        raise


def remote_report(session, start, end, *, tag=TAG):
    if not 0 < (utc(end) - utc(start)).total_seconds() <= 86400:
        raise ValueError("Report window must be positive and at most 24h")
    if not re.fullmatch(r"SponsorAtlas_Task10(?:_[A-Za-z0-9_-]{1,60})?", tag):
        raise ValueError("Exact Task10 tag required")
    joined = session.run(HISTORY_SQL, (WAREHOUSE, tag, start, end))
    attribution = [{**q, "QUERY_ID": q["ATTRIBUTION_QUERY_ID"]} for q in joined
                   if q.get("ATTRIBUTION_QUERY_ID") is not None]
    report = build_report(joined, attribution)
    report["window"] = {"start": start, "end": end, "warehouse": WAREHOUSE, "query_tag": tag}
    report["warehouse_hour_buckets"] = session.run(METERING_SQL, (WAREHOUSE, end, start))
    report["warehouse_metering_scope"] = "whole overlapping hour buckets, all tags including idle"
    return report


def write_json(path, value):
    # Exclusive creation; never overwrite an accepted result or expose credentials.
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


PUBLIC_FACTS = ("fact_lca_case", "fact_job_posting_daily", "fact_wage_benchmark")


def dashboard_sql(schema):
    """Fixed seven metric source queries; separate from Cube physical-refresh evidence."""
    if not re.fullmatch(r"ANALYTICS\.TASK10_[A-Z0-9_]{1,40}", schema):
        raise ValueError("Dedicated Task10 schema required")
    return [
        (f"SELECT COUNT(*) AS CASE_COUNT,AVG(CASE WHEN is_certified THEN 1.0 ELSE 0.0 END) "
        f"AS CERTIFIED_RATE,PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY annual_wage) AS P50,"
        f"PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY annual_wage) AS P75,"
        f"PERCENTILE_CONT(0.9) WITHIN GROUP(ORDER BY annual_wage) AS P90 "
        f"FROM {schema}.fact_lca_case WHERE fiscal_year=%s AND worksite_state=%s"),
        f"SELECT COUNT(*) AS POSTINGS FROM {schema}.fact_job_posting_daily WHERE is_active",
        (f"SELECT COUNT(*) AS BENCHMARK_ROWS,MAX(annual_median_wage) AS OEWS_MEDIAN "
        f"FROM {schema}.fact_wage_benchmark WHERE soc_code='15-1252' AND area_type='1' "
        f"AND area_code='99' AND survey_year=2025"),
    ]


def warehouse_state(session, *, cleanup=False):
    rows = session.run("SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'", cleanup=cleanup)
    if len(rows) != 1 or rows[0]["NAME"] != WAREHOUSE:
        raise ValueError("Exact project warehouse required")
    row = rows[0]
    if int(row["MIN_CLUSTER_COUNT"]) != 1 or int(row["MAX_CLUSTER_COUNT"]) != 1:
        raise ValueError("Single-cluster warehouse required")
    return row


def restore_experiment(session, schema, owner_token, *, report=None, checkpoint=None):
    """Every action attempted, even when a prior cleanup request fails ambiguously."""
    dashboard_sql(schema)  # identifier validation before any SQL
    if not re.fullmatch(r"[a-f0-9]{32}", owner_token):
        raise ValueError("Exact persisted experiment ownership token required")
    checkpoint = checkpoint or (lambda r: None)
    errors = confirm_mutations(session, report, checkpoint)
    if errors:
        return {"status": "OPEN", "errors": errors}
    if not isinstance(session, MutationSession):
        session = MutationSession(session, report, checkpoint)
    try:
        session.run("ALTER WAREHOUSE SPONSORATLAS_XS ABORT ALL QUERIES", cleanup=True)
        schemas = [r for r in session.run(f"SHOW SCHEMAS LIKE '{schema.split('.')[1]}' "
                                          "IN DATABASE ANALYTICS", cleanup=True)
                   if r["NAME"] == schema.split(".")[1]]
        if schemas:
            if len(schemas) != 1 or schemas[0].get("COMMENT") != "Task10:" + owner_token:
                raise ValueError("Experiment ownership changed; refusing object cleanup")
            tables = session.run(f"SHOW TABLES LIKE 'FACT_LCA_CASE' IN SCHEMA {schema}", cleanup=True)
            if any(t["NAME"] == "FACT_LCA_CASE" and t.get("CLUSTER_BY") for t in tables):
                session.run(f"ALTER TABLE {schema}.fact_lca_case SUSPEND RECLUSTER", cleanup=True)
                session.run(f"ALTER TABLE {schema}.fact_lca_case DROP CLUSTERING KEY", cleanup=True)
            session.run(f"DROP SCHEMA {schema}", cleanup=True)
    except Exception as error:  # noqa: BLE001 -- keep ownership/transport uncertainty explicit
        errors.append(type(error).__name__)
        # Even if clustering metadata/stop failed, dropping OUR confirmed schema
        # stops maintenance. Never drop when ownership could not be established.
        if locals().get("schemas") and schemas[0].get("COMMENT") == "Task10:" + owner_token:
            try:
                session.run(f"DROP SCHEMA IF EXISTS {schema}", cleanup=True)
            except Exception as drop_error:  # noqa: BLE001 -- preserve all recovery failures
                errors.append(type(drop_error).__name__)
    for sql in [
        "ALTER WAREHOUSE SPONSORATLAS_XS SET WAREHOUSE_SIZE='XSMALL' AUTO_SUSPEND=60",
        "ALTER SESSION SET USE_CACHED_RESULT=TRUE",
    ]:
        try:
            session.run(sql, cleanup=True)
        except Exception as error:  # noqa: BLE001 -- attempt all cleanup, preserve uncertain status
            errors.append(type(error).__name__)
    try:
        terminal_errors = confirm_mutations(session, report, checkpoint)
        if terminal_errors:
            return {"status": "OPEN", "errors": errors + terminal_errors}
        if warehouse_state(session, cleanup=True)["STATE"] != "SUSPENDED":
            session.run("ALTER WAREHOUSE SPONSORATLAS_XS SUSPEND", cleanup=True)
        state = warehouse_state(session, cleanup=True)
        absent = [r for r in session.run(f"SHOW SCHEMAS LIKE '{schema.split('.')[1]}' "
                                        "IN DATABASE ANALYTICS", cleanup=True)
                  if r["NAME"] == schema.split(".")[1]]
        if (state["SIZE"] != "X-Small" or int(state["AUTO_SUSPEND"]) != 60
                or state["STATE"] != "SUSPENDED" or absent):
            errors.append("RestorationVerificationFailed")
        errors.extend(confirm_mutations(session, report, checkpoint))
    except Exception as error:  # noqa: BLE001 -- uncertainty must block the next experiment
        errors.append(type(error).__name__)
    return {"status": "VERIFIED" if not errors else "OPEN", "errors": errors}


def experiment(session, run_id, fiscal_year, state, *, checkpoint):
    if not re.fullmatch(r"[A-Z0-9_]{1,40}", run_id):
        raise ValueError("Simple unique uppercase experiment id required")
    if not 2000 <= fiscal_year <= 2100 or not re.fullmatch(r"[A-Z]{2}", state):
        raise ValueError("Explicit fiscal year and event state required")
    schema = "ANALYTICS.TASK10_" + run_id
    if any(r["NAME"] == schema.split(".")[1] for r in session.run(
            f"SHOW SCHEMAS LIKE '{schema.split('.')[1]}' IN DATABASE ANALYTICS")):
        raise ValueError("Experiment namespace already exists")
    before = warehouse_state(session)
    if before["SIZE"] != "X-Small" or int(before["AUTO_SUSPEND"]) != 60:
        raise ValueError("Initial defaults must be XS/60")
    # Warehouse monitor covers compute only; serverless clustering has a separate opt-in allowance.
    monitor = before.get("RESOURCE_MONITOR")
    if not monitor or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", monitor):
        raise ValueError("Pre-provisioned project credit monitor required")
    monitors = session.run(f"SHOW RESOURCE MONITORS LIKE '{monitor}'")
    allowance = float(os.environ["SPONSORATLAS_COST_MAX_CREDITS"])
    if (len(monitors) != 1 or monitors[0].get("NAME") != monitor
            or monitors[0].get("LEVEL") != "WAREHOUSE"
            or not 0 < float(monitors[0]["CREDIT_QUOTA"]) <= allowance
            or not 0 < float(str(monitors[0].get("SUSPEND_IMMEDIATELY_AT", "0")).rstrip("%")) <= 100
            or float(monitors[0].get("USED_CREDITS", 0)) >= float(monitors[0]["CREDIT_QUOTA"])):
        raise ValueError("Monitor must have immediate suspension within explicit run allowance")
    clustering_allowance = number(os.environ.get("SPONSORATLAS_CLUSTERING_MAX_CREDITS"))
    if clustering_allowance is None or not 0 < clustering_allowance <= allowance:
        raise ValueError("Explicit separate clustering allowance required")
    report = {"kind": "ACTUAL_SNOWFLAKE_SOURCE_REFRESH_EXPERIMENT", "schema": schema,
              "mutation_protocol": 1, "originating_session": int(session.session_id), "mutations": [],
              "owner_token": uuid.uuid4().hex,
              "started_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "samples": [], "query_ids": [], "status": "RUNNING",
              "cube_end_to_end": "OPEN: requires independently verified controlled physical refresh",
              "clustering_allowance_credits": clustering_allowance,
              "clustering_budget_scope": "operator allowance; serverless billing is delayed, not a hard cap"}
    checkpoint(report)  # durable pending barrier BEFORE dispatching the first mutation
    session = MutationSession(session, report, checkpoint)
    try:
        session.run(f"CREATE SCHEMA {schema} COMMENT='Task10:{report['owner_token']}'")
        for fact in (*PUBLIC_FACTS, "dim_occupation", "dim_geography", "dim_date"):
            session.run(f"CREATE TRANSIENT TABLE {schema}.{fact} CLONE ANALYTICS.MARTS.{fact}")
        tables = session.run(f"SHOW TABLES LIKE 'FACT_LCA_CASE' IN SCHEMA {schema}")
        if any(t["NAME"] == "FACT_LCA_CASE" and t.get("CLUSTER_BY") for t in tables):
            session.run(f"ALTER TABLE {schema}.fact_lca_case DROP CLUSTERING KEY")
        fingerprint_sql = [f"SELECT COUNT(*) AS N,HASH_AGG(*) AS H FROM {schema}.{fact}"
                           for fact in (*PUBLIC_FACTS, "dim_occupation", "dim_geography", "dim_date")]
        fingerprints = [session.run(sql) for sql in fingerprint_sql]
        report["source_fingerprints"] = fingerprints
        queries = dashboard_sql(schema)
        expected = None

        def refresh(label, cached):
            nonlocal expected
            session.run(f"ALTER SESSION SET USE_CACHED_RESULT={'TRUE' if cached else 'FALSE'}")
            session.run(f"ALTER SESSION SET QUERY_TAG='{TAG}_{run_id}_{label}'")
            started, results, ids = time.monotonic(), [], []
            for i, sql in enumerate(queries):
                results.append(session.run(sql, (fiscal_year, state) if i == 0 else None))
                ids.append(session.query_ids[-1])
            elapsed = time.monotonic() - started
            digest = hashlib.sha256(json.dumps(results, sort_keys=True, default=str).encode()).hexdigest()
            if expected is None:
                expected = digest
            if digest != expected:
                raise ValueError("Business results changed across refresh phases")
            if not results[0][0]["CASE_COUNT"]:
                raise ValueError("LCA filter must match real rows")
            if results[2][0]["BENCHMARK_ROWS"] != 1:
                raise ValueError("OEWS benchmark grain is not unique")
            report["samples"].append({"phase": label, "elapsed_seconds": elapsed,
                                      "measured_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "query_tag": f"{TAG}_{run_id}_{label}",
                                      "query_ids": ids, "result_sha256": digest,
                                      "use_cached_result": cached, "credits": None,
                                      "credits_status": "PENDING_ACCOUNT_USAGE_8H",
                                      "persisted_result_hit": "UNKNOWN",
                                      "warehouse": warehouse_state(session)["SIZE"]})
            cube = cube_refresh(schema, run_id, label, cached, session.deadline - session.clock())
            if report.get("cube_result_sha256") not in (None, cube["result_sha256"]):
                raise ValueError("Cube business results changed across phases")
            report["cube_result_sha256"] = cube["result_sha256"]
            report["samples"][-1]["cube"] = cube
            report["cube_end_to_end"] = "MEASURED_ATTRIBUTION_PENDING"
            checkpoint(report)

        refresh("baseline", False)
        session.run(f"ALTER TABLE {schema}.fact_lca_case CLUSTER BY (fiscal_year,worksite_state)")
        session.run(f"ALTER TABLE {schema}.fact_lca_case RESUME RECLUSTER")
        # Capture the actual state; no claim that asynchronous reclustering completed.
        report["clustering_information"] = session.run(
            "SELECT PARSE_JSON(SYSTEM$CLUSTERING_INFORMATION(%s)) AS INFO",
            (schema + ".FACT_LCA_CASE",))
        report["clustering_completion"] = "UNKNOWN: key configured; asynchronous maintenance not presumed complete"
        refresh("clustered", False)
        session.run("ALTER WAREHOUSE SPONSORATLAS_XS SET WAREHOUSE_SIZE='SMALL' WAIT_FOR_COMPLETION=TRUE")
        refresh("small", False)
        session.run("ALTER WAREHOUSE SPONSORATLAS_XS SET WAREHOUSE_SIZE='XSMALL' WAIT_FOR_COMPLETION=TRUE")
        refresh("cache_prime", True)
        refresh("cache_repeat", True)
        after = [session.run(sql) for sql in fingerprint_sql]
        if after != fingerprints:
            raise ValueError("Experiment data changed")
        report["status"] = "MEASURED_ATTRIBUTION_PENDING"
    finally:
        report["query_ids"] = list(session.query_ids)
        report["restoration"] = restore_experiment(session, schema, report["owner_token"],
                                                    report=report, checkpoint=checkpoint)
        report["ended_at_utc"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if report["restoration"]["status"] != "VERIFIED":
            report["status"] = "RECOVERY_REQUIRED"
        elif report["status"] == "RUNNING":
            report["status"] = "FAILED_RESTORED"
        checkpoint(report)
    return report


def durable_checkpoint(path, report):
    durable_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, default=str, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_directory(path):
    path = Path(path).absolute()
    chain = [path, *path.parents]
    for directory in reversed(chain):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("Trusted directory chain required")
    # Every observer independently syncs the complete visible ancestry, even
    # when another creator paused between mkdir and its parent fsync.
    for directory in chain:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def cube_refresh(schema, run_id, label, cached, remaining):
    if remaining <= 60:
        raise TimeoutError("Insufficient absolute budget for private Cube refresh")
    # Fixed program, no shell or arbitrary command hook. A fresh work path per phase
    # forces distinct physical rollups; cache reuse can only occur at Snowflake.
    work = ROOT / ".tools/task10/cube" / run_id / label
    if work.exists():
        raise FileExistsError("Cube phase work must be fresh")
    work.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SPONSORATLAS_COST_SCHEMA": schema,
           "SPONSORATLAS_COST_CUBE_WORK": str(work),
           "SPONSORATLAS_COST_PHASE": label,
           "SPONSORATLAS_COST_RUN_ID": run_id,
           "SPONSORATLAS_COST_CACHE": "TRUE" if cached else "FALSE",
           "SPONSORATLAS_COST_SECONDS": str(min(60, int(remaining - 10)))}
    command = [str(ROOT / ".tools/node-runtime/node-v24.22.1-linux-x64/bin/node"),
               "--max-old-space-size=512", str(ROOT / "semantic/scripts/cost-refresh.cjs")]
    result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                            timeout=min(75, remaining), check=False)
    if result.returncode:
        raise RuntimeError("Private Cube refresh failed; details suppressed")
    evidence = json.loads((work / "cost-refresh.json").read_text())
    if evidence.get("kind") != "ACTUAL_SNOWFLAKE_CUBE_REFRESH" or not evidence.get("query_ids"):
        raise ValueError("Actual tagged Cube query IDs required")
    return evidence


def reconcile_experiment(session, evidence, start, end):
    """Late account-usage collection; totals stay NULL if any exact query is missing."""
    if evidence.get("kind") != "ACTUAL_SNOWFLAKE_SOURCE_REFRESH_EXPERIMENT":
        raise ValueError("Actual experiment evidence required")
    if not 0 < (utc(end) - utc(start)).total_seconds() <= 86400:
        raise ValueError("Positive reconciliation window <=24h required")
    dashboard_sql(evidence["schema"])
    run_id = evidence["schema"].split("TASK10_", 1)[1]
    result = json.loads(json.dumps(evidence))
    for sample in result["samples"]:
        for scope in (sample, sample.get("cube", {})):
            ids = scope.get("query_ids", [])
            if not ids or len(ids) > 60 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", q) for q in ids):
                raise ValueError("Bounded actual query IDs required")
            placeholders = ",".join(["%s"] * len(ids))
            # Restrict even supplied evidence to this project warehouse/tag and window.
            tag = scope.get("query_tag", "")
            phase = sample.get("phase", "")
            if phase not in {"baseline", "clustered", "small", "cache_prime", "cache_repeat"}:
                raise ValueError("Known experiment phase required")
            expected = f"{TAG}_{run_id}_{phase}" + ("_cube" if scope is not sample else "")
            if tag != expected:
                raise ValueError("Validated exact phase query tag required")
            sql = HISTORY_SQL.replace(
                "ORDER BY a.credits_attributed_compute DESC NULLS LAST,q.query_id\nLIMIT 20",
                f"AND q.query_id IN ({placeholders})")
            joined = session.run(sql, (WAREHOUSE, tag, start, end, *ids))
            attribution = [{**q, "QUERY_ID": q["ATTRIBUTION_QUERY_ID"]} for q in joined
                           if q.get("ATTRIBUTION_QUERY_ID")]
            report = build_report(joined, attribution, limit=60)
            available = {r["query_id"]: r for r in report["queries"]}
            complete = all(q in available and available[q]["credits_attributed_compute"] is not None for q in ids)
            scope["credits"] = sum(available[q]["credits_attributed_compute"] for q in ids) if complete else None
            scope["credits_status"] = "COMPLETE_QUERY_COMPUTE_ONLY" if complete else "INCOMPLETE_DELAY_OR_SHORT_QUERY"
            scope["query_profile"] = report
    result["warehouse_hour_buckets"] = session.run(METERING_SQL, (WAREHOUSE, end, start))
    result["clustering_history"] = session.run(
        "SELECT start_time,end_time,credits_used,num_bytes_reclustered,version "
        "FROM SNOWFLAKE.ACCOUNT_USAGE.AUTOMATIC_CLUSTERING_HISTORY "
        "WHERE database_name='ANALYTICS' AND schema_name=%s AND table_name='FACT_LCA_CASE' "
        "AND start_time >= %s AND start_time < %s ORDER BY start_time LIMIT 100",
        (evidence["schema"].split(".")[1], start, end))
    result["billing_scope"] = "query compute, whole warehouse hours, and serverless maintenance separate; no dollar conversion"
    return result


def remote_experiment(args):
    # One durable pending barrier for ALL Task10 remote mutations on this host.
    directory = ROOT / ".tools/task10"
    durable_directory(directory)
    journal = directory / "remote-pending.json"
    with (directory / "remote.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = json.loads(journal.read_text()) if journal.exists() else None
        if args.mode == "experiment" and prior and not journal_cleared(prior):
            raise RuntimeError("Prior experiment unresolved; recover exact journal first")
        if args.mode == "recover" and not prior:
            raise ValueError("No exact pending experiment to recover")
        session = connect(experiment=True)
        try:
            if args.mode == "recover":
                prior["restoration"] = restore_experiment(
                    session, prior["schema"], prior["owner_token"], report=prior,
                    checkpoint=lambda r: durable_checkpoint(journal, r))
                prior["status"] = "RECOVERED" if prior["restoration"]["status"] == "VERIFIED" else "RECOVERY_REQUIRED"
                durable_checkpoint(journal, prior)
                write_json(args.output, prior)
            else:
                report = experiment(session, args.run_id, args.fiscal_year, args.state,
                                    checkpoint=lambda r: durable_checkpoint(journal, r))
                write_json(args.output, report)
        finally:
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("input", type=Path)
    fixture.add_argument("output", type=Path)
    remote = sub.add_parser("remote")
    remote.add_argument("--start", required=True)
    remote.add_argument("--end", required=True)
    remote.add_argument("--tag", default=TAG)
    remote.add_argument("--output", required=True, type=Path)
    mutable = sub.add_parser("experiment")
    mutable.add_argument("--run-id", required=True)
    mutable.add_argument("--fiscal-year", required=True, type=int)
    mutable.add_argument("--state", required=True)
    mutable.add_argument("--output", required=True, type=Path)
    recovery = sub.add_parser("recover")
    recovery.add_argument("--output", required=True, type=Path)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("input", type=Path)
    reconcile.add_argument("--start", required=True)
    reconcile.add_argument("--end", required=True)
    reconcile.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Evidence output must be new before any remote dispatch")
    if args.mode == "fixture":
        if args.input.stat().st_size > 2_000_000:
            raise ValueError("Fixture byte cap exceeded")
        data = json.loads(args.input.read_text())
        if data.get("kind") != "SYNTHETIC_QUERY_HISTORY":
            raise ValueError("Explicit synthetic fixture label required")
        write_json(args.output, build_report(data["QUERY_HISTORY"],
                                            data["QUERY_ATTRIBUTION_HISTORY"], synthetic=True))
    elif args.mode in ("remote", "reconcile"):
        session = connect()
        try:
            if args.mode == "reconcile":
                if args.input.stat().st_size > 2_000_000:
                    raise ValueError("Evidence byte cap exceeded")
                report = reconcile_experiment(session, json.loads(args.input.read_text()),
                                              args.start, args.end)
            else:
                report = remote_report(session, args.start, args.end, tag=args.tag)
            write_json(args.output, report)
        finally:
            session.close()
    else:
        remote_experiment(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- never print credentials or connector exceptions
        raise SystemExit(f"Cost operation failed ({type(error).__name__}); remote acceptance OPEN") from None
