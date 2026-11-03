"""Fail-closed Azure cleanup limited to the SponsorAtlas resource group.

No creation, scheduling, account switching, or configurable deletion scope. Azure
has no atomic tag-conditioned delete: re-read immediately before each mutation.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT = "sponsoratlas"
OWNER = "codex-sponsoratlas"
GROUP = "rg-sponsoratlas-lab"
ROOT = Path(__file__).resolve().parents[1]
UTC_TTL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)")
ID_SEGMENT = re.compile(r"[A-Za-z0-9_.() -]+")


class CleanupError(Exception):
    """A CLI or identity failure that must result in a nonzero exit."""


def log(event, *, error=False, **details):
    print(json.dumps({"event": event, **details}), file=sys.stderr if error else sys.stdout)


def configure_environment():
    # A supplied config must still belong to this project, never another project
    # or ~/.azure. Keep all CLI writes and extension caches under .tools.
    config = Path(os.environ.get("AZURE_CONFIG_DIR", ROOT / ".tools/azure")).resolve()
    tools = (ROOT / ".tools").resolve()
    if config == tools or not config.is_relative_to(tools):
        raise CleanupError("AZURE_CONFIG_DIR must be a directory under this project's .tools")
    os.environ["AZURE_CONFIG_DIR"] = str(config)
    os.environ["AZURE_EXTENSION_DIR"] = str(config / "extensions")
    os.environ["AZURE_CORE_COLLECT_TELEMETRY"] = "false"


class Azure:
    def __init__(self):
        account = self.run(["account", "show"])
        if not isinstance(account, dict) or account.get("state") != "Enabled":
            raise CleanupError("current Azure account must be Enabled")
        subscription = account.get("id")
        try:
            if (
                not isinstance(subscription, str)
                or str(uuid.UUID(subscription)) != subscription.lower()
            ):
                raise ValueError
        except ValueError as exc:
            raise CleanupError("current Azure subscription id is not a canonical UUID") from exc
        self.subscription = subscription
        self.group_id = f"/subscriptions/{subscription}/resourceGroups/{GROUP}"

    def run(self, arguments, *, mutation=False):
        command = ["az", *arguments]
        if arguments[:2] != ["account", "show"]:
            command.extend(["--subscription", self.subscription])
        command.extend(["--output", "json", "--only-show-errors"])
        operation = " ".join(arguments[:2])
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            # Do not echo stdout/stderr or exception details that could contain credentials.
            raise CleanupError(
                f"Azure CLI {operation} could not complete ({type(exc).__name__})"
            ) from exc
        if result.returncode:
            raise CleanupError(f"Azure CLI {operation} failed (exit {result.returncode})")
        if mutation:
            return None
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise CleanupError(f"Azure CLI {operation} returned invalid JSON") from exc

    def exists(self):
        exists = self.run(["group", "exists", "--name", GROUP])
        if not isinstance(exists, bool):
            raise CleanupError("Azure CLI group exists must return a JSON boolean")
        return exists

    def resource_id_reason(self, resource_id):
        if not isinstance(resource_id, str) or not resource_id.startswith("/"):
            return "invalid_resource_id"
        parts = resource_id[1:].split("/")
        if any(not ID_SEGMENT.fullmatch(part) or part in {".", ".."} for part in parts):
            return "invalid_resource_id"
        prefix = self.group_id + "/providers/"
        if not resource_id.lower().startswith(prefix.lower()):
            return "resource_scope_mismatch"
        tail = resource_id[len(prefix) :].split("/")
        if len(tail) < 3 or len(tail) % 2 != 1:
            return "invalid_resource_id"
        return None

    def eligible_reason(self, resource):
        if not isinstance(resource, dict):
            return "invalid_resource_record"
        reason = self.resource_id_reason(resource.get("id"))
        if reason:
            return reason
        tags = resource.get("tags")
        if not isinstance(tags, dict):
            return "missing_or_invalid_tags"
        if tags.get("project") != PROJECT:
            return "project_tag_mismatch"
        if tags.get("owner") != OWNER:
            return "owner_tag_mismatch"
        ttl = tags.get("ttl")
        if not isinstance(ttl, str) or not UTC_TTL.fullmatch(ttl):
            return "missing_or_invalid_utc_ttl"
        try:
            expires = datetime.fromisoformat(ttl)
        except ValueError:
            return "missing_or_invalid_utc_ttl"
        if expires > datetime.now(UTC):
            return "ttl_not_expired"
        return None

    def reap(self, apply):
        resources = self.run(["resource", "list", "--resource-group", GROUP])
        if not isinstance(resources, list):
            raise CleanupError("Azure CLI resource list must return a JSON array")
        failed = False
        for candidate in resources:
            resource_id = candidate.get("id") if isinstance(candidate, dict) else None
            reason = self.eligible_reason(candidate)
            if reason:
                log("skipped", resource_id=resource_id, reason=reason)
                continue
            try:
                current = self.run(["resource", "show", "--ids", resource_id])
                if not isinstance(current, dict):
                    raise CleanupError("Azure CLI resource show must return a JSON object")
                reason = self.eligible_reason(current)
                if not reason and current["id"].lower() != resource_id.lower():
                    reason = "resource_identity_changed"
                if reason:
                    log("skipped", resource_id=resource_id, reason=f"revalidation_{reason}")
                    continue
                if apply:
                    self.run(["resource", "delete", "--ids", resource_id], mutation=True)
                    log("deleted", resource_id=resource_id)
                else:
                    log("would_delete", resource_id=resource_id)
            except CleanupError as exc:
                failed = True
                log("error", error=True, resource_id=resource_id, reason=str(exc))
        log("reap_complete", mode="apply" if apply else "dry-run", failed=failed)
        return 1 if failed else 0

    def checked_group(self):
        group = self.run(["group", "show", "--name", GROUP])
        if not isinstance(group, dict) or not isinstance(group.get("id"), str):
            raise CleanupError("group identity missing or invalid")
        if group["id"].lower() != self.group_id.lower():
            raise CleanupError("group identity does not match fixed project scope")
        tags = group.get("tags")
        if (
            not isinstance(tags, dict)
            or tags.get("project") != PROJECT
            or tags.get("owner") != OWNER
        ):
            raise CleanupError("group project/owner tags do not match; refusing deletion")

    def down(self, apply):
        self.checked_group()
        if apply:
            self.checked_group()
            self.run(["group", "delete", "--name", GROUP, "--yes"], mutation=True)
            log("group_deleted", group=GROUP)
        else:
            log("would_delete_group", group=GROUP)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute deletions (default: dry-run)")
    parser.add_argument("--down", action="store_true", help="delete the fixed tagged project group")
    args = parser.parse_args(argv)
    try:
        configure_environment()
        azure = Azure()
        log(
            "cleanup_start",
            group=GROUP,
            mode="apply" if args.apply else "dry-run",
            operation="down" if args.down else "reap",
        )
        if not azure.exists():
            log("group_missing", group=GROUP)
            return 0
        return azure.down(args.apply) if args.down else azure.reap(args.apply)
    except CleanupError as exc:
        log("error", error=True, reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
