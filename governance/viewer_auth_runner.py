"""Explicit once-only local viewer repair. Native use needs separate root approval.

The recovery SQL builder is offline only: there is no SQL-write execution path.
This module never starts services, changes assignments or rotates credentials.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from governance.viewer_auth import (
    Client,
    Directory,
    Failure,
    canonical_uuid,
    credentials,
    require,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".tools/governance"
RUN_ID = "governance-viewer-auth-20261025-once1"
PRIVATE = RUNTIME / "viewer-auth-20261025"
PUBLIC = ROOT / "docs/evidence/governance-viewer-auth-20261025"
PG = "f1d1d9aea4ae520ec2c0234a12781e7da5e79541f45a1499728ec7c90d736aa0"
SOURCE_FILES = ("governance/viewer_auth.py", "governance/viewer_auth_runner.py",
                "governance/initialize_catalog.py")
PROJECTION_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '2000ms';
SET LOCAL lock_timeout = '1000ms';
WITH target AS (
 SELECT id, xmin::text AS row_version, json::jsonb AS j FROM user_entity
 WHERE name='sponsoratlas-viewer' AND email='sponsoratlas-viewer@example.invalid' LIMIT 2
)
SELECT json_build_object('matches',(SELECT count(*) FROM target),
 'checks',(SELECT json_agg(json_build_object(
 'id',id, 'row_version',row_version,
 'name_matches',j->>'name'='sponsoratlas-viewer',
 'email_matches',j->>'email'='sponsoratlas-viewer@example.invalid',
 'is_admin',j->'isAdmin','is_bot',j->'isBot','deleted',j->'deleted',
 'mechanism_present',COALESCE(j->'authenticationMechanism'<>'null'::jsonb,false),
 'basic_type',j#>>'{authenticationMechanism,authType}'='BASIC',
 'password_present',COALESCE(length(j#>>'{authenticationMechanism,config,password}')>0,false),
 'auth_field_existed',j?'authenticationMechanism',
 'original_auth_absent_or_null',j->'authenticationMechanism' IS NULL
   OR j->'authenticationMechanism'='null'::jsonb
 )) FROM target));
ROLLBACK;
"""


def row_version(value):
    require(isinstance(value, str) and value.isascii() and value.isdecimal()
            and 0 < len(value) <= 10 and 0 < int(value) < 2**32, "ROW_VERSION")
    return value


def parse_projection(raw):
    try:
        require(len(raw) <= 8192, "DB_PROJECTION")
        data = json.loads(raw)
        require(isinstance(data, dict) and type(data.get("matches")) is int
                and data["matches"] == 1 and isinstance(data.get("checks"), list)
                and len(data["checks"]) == 1, "DB_PROJECTION")
        check = data["checks"][0]
        require(isinstance(check, dict), "DB_PROJECTION")
        canonical_uuid(check.get("id"))
        row_version(check.get("row_version"))
        require(check.get("name_matches") is True and check.get("email_matches") is True
                and all(check.get(k) is False for k in ("is_admin", "is_bot", "deleted")),
                "DB_IDENTITY")
        for key in ("mechanism_present", "password_present", "auth_field_existed",
                    "original_auth_absent_or_null"):
            require(type(check.get(key)) is bool, "DB_PROJECTION")
        if check["mechanism_present"]:
            require(check.get("basic_type") is True and check["password_present"]
                    and check["auth_field_existed"] and not check["original_auth_absent_or_null"],
                    "AUTH_STATE")
        else:
            require(check.get("basic_type") in (None, False) and not check["password_present"]
                    and check["original_auth_absent_or_null"], "AUTH_STATE")
        return check
    except (ValueError, TypeError, UnicodeError):
        raise Failure("DB_PROJECTION") from None


def inspect_database():
    """Fixed readonly metadata projection; capture all output without printing it."""
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", PG, "psql", "-X", "-q", "-A", "-t", "-v",
             "ON_ERROR_STOP=1", "-U", "sponsoratlas_om", "-d", "sponsoratlas_om"],
            input=PROJECTION_SQL.encode(), capture_output=True, timeout=5, check=False)
        require(result.returncode == 0 and not result.stderr, "DB_READ_FAILED")
        return parse_projection(result.stdout)
    except (OSError, subprocess.SubprocessError):
        raise Failure("DB_READ_FAILED") from None


def save(directory, name, value):
    directory.write(name, json.dumps(value, sort_keys=True).encode(), replace=directory.exists(name))


def repair(api, password, original_bytes, directory, inspect):
    """Return allowlisted public facts; all identity/assignment receipts stay private."""
    require(credentials(original_bytes) == password, "CREDENTIAL_SHAPE")
    directory.write("attempt.json", json.dumps({"run_id": RUN_ID, "phase": "CLAIMED"}).encode())
    state = {"schema_version": 1, "run_id": RUN_ID, "phase": "PREFLIGHT", "api_gate": "OPEN",
             "cause": "UNRESOLVED", "mutation_attempted": False, "mutation_succeeded": False,
             "mutation_ambiguous": False, "baseline_status_code": None, "post_login_status_code": None,
             "identity_verified": False, "role_team_domain_parity": False,
             "native_ui_gate": "NOT_RUN_BY_WORKER", "recovery_available": False,
             "credential_file_changed": False, "failure_code": None,
             "session_policy_validated": False}
    save(directory, "state.json", state)
    try:
        api.preflight()
        state["session_policy_validated"] = True
        before = api.viewer()
        metadata = inspect()
        require(metadata["id"] == before["id"], "DB_IDENTITY")
        directory.write("original-credentials.json", original_bytes)
        save(directory, "assignments.json", {"before": before})
        receipt = None
        if not metadata["mechanism_present"]:
            receipt = {"viewer_id": before["id"], "auth_field_existed": metadata["auth_field_existed"],
                       "original_value": None, "row_version_before": metadata["row_version"],
                       "row_version_after": None}
            save(directory, "auth-recovery.json", receipt)
        state.update(phase="BASELINE", mechanism_before_present=metadata["mechanism_present"])
        save(directory, "state.json", state)
        baseline = api.login(password, before)
        state["baseline_status_code"] = baseline
        if baseline == 200:
            require(metadata["mechanism_present"], "AUTH_STATE_INCONSISTENT")
            require(api.viewer() == before, "IDENTITY_PARITY")
            state.update(api_gate="PASS_NO_REPAIR", phase="COMPLETE", identity_verified=True,
                         role_team_domain_parity=True, cause="HISTORICAL_FAILURE_NOT_REPRODUCED")
            return state
        require(baseline == 401, "BASELINE_UNEXPECTED")
        require(receipt is not None, "EXISTING_AUTH_UNRESOLVED")
        require(api.viewer() == before, "IDENTITY_PARITY")
        state.update(phase="MUTATION_ATTEMPTED", cause="MISSING_BASIC_MECHANISM",
                     mutation_attempted=True, mutation_ambiguous=True)
        save(directory, "state.json", state)  # Durable intent BEFORE the only password write.
        api.change_password(password)
        state.update(mutation_succeeded=True, mutation_ambiguous=False, phase="POSTCHECK")
        save(directory, "state.json", state)
        after = api.viewer()
        require(after == before, "IDENTITY_PARITY")
        code = api.login(password, before)
        state["post_login_status_code"] = code
        require(code == 200, "POST_LOGIN_FAILED")
        require(api.viewer() == before, "IDENTITY_PARITY")
        after_metadata = inspect()
        require(after_metadata["id"] == before["id"] and after_metadata["mechanism_present"]
                and after_metadata["basic_type"] and after_metadata["password_present"],
                "POST_AUTH_STATE")
        receipt["row_version_after"] = after_metadata["row_version"]
        save(directory, "auth-recovery.json", receipt)
        save(directory, "assignments.json", {"before": before, "after": after})
        state.update(api_gate="PASS", phase="COMPLETE", identity_verified=True,
                     role_team_domain_parity=True, recovery_available=True,
                     mechanism_after_basic=True, password_after_present=True)
        return state
    except Failure as error:
        state.update(phase="FAILED", failure_code=error.code)
        raise
    finally:
        save(directory, "state.json", state)


def recovery_sql(receipt):
    """Build, NEVER execute, one stopped-OM auth-field CAS for separate review.

    xmin drift refuses the transaction. Audit/login/session/history effects remain.
    Original state must have been absent/null: no password or hash backup is accepted.
    """
    require(isinstance(receipt, dict) and set(receipt) == {
        "viewer_id", "auth_field_existed", "original_value", "row_version_before", "row_version_after"
    }, "RECOVERY_RECEIPT")
    identifier = canonical_uuid(receipt["viewer_id"])
    require(type(receipt["auth_field_existed"]) is bool and receipt["original_value"] is None,
            "RECOVERY_RECEIPT")
    row_version(receipt["row_version_before"])
    version = row_version(receipt["row_version_after"])
    expression = ("jsonb_set(json::jsonb, '{authenticationMechanism}', 'null'::jsonb, true)"
                  if receipt["auth_field_existed"] else "json::jsonb - 'authenticationMechanism'")
    # UUID/xid are validated canonical characters; no secrets or caller SQL enter this script.
    return f"""BEGIN;
SET LOCAL statement_timeout='2000ms';
SET LOCAL lock_timeout='1000ms';
DO $restore$ DECLARE affected integer; BEGIN
 UPDATE user_entity SET json={expression}
 WHERE id::text='{identifier}' AND name='sponsoratlas-viewer'
 AND email='sponsoratlas-viewer@example.invalid' AND xmin = '{version}'::xid
 AND json::jsonb->'isAdmin'='false'::jsonb AND json::jsonb->'isBot'='false'::jsonb
 AND json::jsonb->'deleted'='false'::jsonb
 AND json::jsonb#>>'{{authenticationMechanism,authType}}'='BASIC'
 AND COALESCE(length(json::jsonb#>>'{{authenticationMechanism,config,password}}'),0)>0;
 GET DIAGNOSTICS affected = ROW_COUNT;
 IF affected <> 1 THEN RAISE EXCEPTION 'CAS_MISMATCH'; END IF;
END; $restore$;
COMMIT;
"""


def source_binding():
    """Hash only the three named public source files; never private/runtime files."""
    binding = hashlib.sha256()
    for name in SOURCE_FILES:
        binding.update(name.encode() + b"\0" + (ROOT / name).read_bytes() + b"\0")
    return binding.hexdigest()


def failure_result(directory, code):
    """Keep durable mutation facts on failure; unreadable state means unknown."""
    result = {"api_gate": "OPEN", "native_ui_gate": "NOT_RUN_BY_WORKER", "phase": "FAILED",
              "failure_code": code, "mutation_attempted": None, "mutation_succeeded": None,
              "mutation_ambiguous": None}
    try:
        state = json.loads(directory.read("state.json", 8192))
        require(isinstance(state, dict), "STATE_SHAPE")
        for key in ("mutation_attempted", "mutation_succeeded", "mutation_ambiguous",
                    "identity_verified", "role_team_domain_parity", "recovery_available",
                    "credential_file_changed", "session_policy_validated"):
            if type(state.get(key)) is bool:
                result[key] = state[key]
        for key in ("baseline_status_code", "post_login_status_code"):
            if type(state.get(key)) is int and state[key] in (200, 401):
                result[key] = state[key]
        if state.get("cause") in ("UNRESOLVED", "MISSING_BASIC_MECHANISM",
                                  "HISTORICAL_FAILURE_NOT_REPRODUCED"):
            result["cause"] = state["cause"]
    except (Failure, ValueError, TypeError, UnicodeError):
        pass
    return result


def run_native(deadline_epoch, expected_binding):
    """Only the separately approved CLI can reach actual credentials/API/readonly SQL."""
    require(source_binding() == expected_binding, "SOURCE_BINDING")
    remaining = deadline_epoch - time.time()
    require(0 < remaining <= 90, "DEADLINE")
    api = None
    result = {"api_gate": "OPEN", "native_ui_gate": "NOT_RUN_BY_WORKER", "failure_code": "INCOMPLETE"}
    with Directory(PRIVATE, create=True) as directory:
        save(directory, "lease.json", {"run_id": RUN_ID, "deadline_epoch": deadline_epoch,
                                       "source_binding": expected_binding})
        try:
            with Directory(RUNTIME, private=False) as existing:
                raw = existing.read("ui-credentials.json")
            password = credentials(raw)
            from governance.local_auth import token
            api = Client(token=token(), deadline=time.monotonic() + remaining)
            result = repair(api, password, raw, directory, inspect_database)
            return 0
        except Failure as error:
            result = failure_result(directory, error.code)
            return 1
        except Exception:  # noqa: BLE001 - Public boundary must not expose secret-bearing exceptions.
            result = failure_result(directory, "INTERNAL")
            return 1
        finally:
            if api is not None:
                api.close()
            result.update(run_id=RUN_ID, source_binding=expected_binding,
                          rollback_scope="AUTH_FIELD_ONLY_SEPARATE_APPROVAL",
                          cleanup_status="ROOT_OWNS_SERVICES")
            with Directory(PUBLIC, private=False) as public:
                public.write("actual-once1.json", json.dumps(result, sort_keys=True).encode())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved-native-once", choices=[RUN_ID], required=True)
    parser.add_argument("--deadline-epoch", type=float, required=True)
    parser.add_argument("--source-binding", required=True)
    args = parser.parse_args(argv)
    try:
        return run_native(args.deadline_epoch, args.source_binding)
    except Exception:  # noqa: BLE001 - Last CLI boundary emits only a fixed failure code.
        print("VIEWER_AUTH_FAILED_CLOSED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
