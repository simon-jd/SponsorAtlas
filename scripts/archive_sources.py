"""Archive an official tabular release and materialize provenance-rich JSONL for Airbyte.

No network or Snowflake writes beyond explicit public-source GET requests. Run as
python -m scripts.archive_sources ingestion/releases.json --release lca_fy2024_q4.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.source_files import iter_rows

OFFICIAL_HOSTS = {"LCA": {"www.dol.gov"}, "USCIS": {"www.uscis.gov"}, "BLS": {"www.bls.gov"}}
MAX_DOWNLOAD_BYTES = 2 * 1024**3


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def official_url(source, url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in OFFICIAL_HOSTS.get(source, set())
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Release URL must use the official source HTTPS host")
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, source):
        self.source = source

    def redirect_request(self, request, fp, code, msg, hdrs, newurl):
        official_url(self.source, newurl)
        return super().redirect_request(request, fp, code, msg, hdrs, newurl)


def download_release(release, directory):
    if release.get("requires_official_release_url") and not release.get("url"):
        raise ValueError(
            "requires_official_release_url: confirm annual CSV link from USCIS publisher"
        )
    url = official_url(release["source"], release.get("url", ""))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=".download-")
    path = Path(name)
    try:
        opener = urllib.request.build_opener(OfficialRedirect(release["source"]))
        request = urllib.request.Request(
            url, headers={"User-Agent": "SponsorAtlas/0.1 release archive"}
        )
        with os.fdopen(fd, "wb") as output, opener.open(request, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                raise ValueError("Expected data file; received HTML")
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Release exceeds maximum download size")
                output.write(chunk)
            if total == 0:
                raise ValueError("Empty source release")
            http = {
                "retrieved_at": datetime.now(UTC).isoformat(),
                "final_url": response.url,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "content_type": content_type,
            }
        return path, http
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def archive_release(release, downloaded, root, http=None):
    """Immutable original + derived artifact. Local fixtures may call this directly."""
    source, period = release["source"], release["release"]
    if release["format"] not in {"csv", "xlsx"}:
        raise ValueError("Original official releases must be CSV or XLSX (optionally inside ZIP)")
    if source not in OFFICIAL_HOSTS or not re.fullmatch(r"FY20\d{2}(?:_Q[1-4])?", period):
        raise ValueError("Expected supported source and FYyyyy[_Qn] period")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", release["id"]):
        raise ValueError("Invalid release id")
    from scripts.reconcile_counts import TABLE

    table = f"RAW.{source}.{release['id'].upper()}"
    if not TABLE.fullmatch(table):
        raise ValueError("Invalid release table")
    sha = sha256_file(downloaded)
    if release.get("expected_sha256") and sha != release["expected_sha256"]:
        raise ValueError("Publisher/operator-pinned SHA256 mismatch")
    extension = ".zip" if release.get("zip_member") else "." + release["format"]
    directory = Path(root).resolve() / source.lower() / period
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{release['id'].upper()}__{sha}"
    original = directory / (stem + extension)

    # Hard-link installation is atomic and refuses to overwrite an existing hash path.
    def install(temp, final):
        try:
            os.link(temp, final)
        except FileExistsError:
            if sha256_file(temp) != sha256_file(final):
                raise ValueError("Immutable archive path has different content") from None

    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".original-")
    with os.fdopen(fd, "wb") as target, Path(downloaded).open("rb") as incoming:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            target.write(chunk)
    temp = Path(temp_name)
    try:
        install(temp, original)
    finally:
        temp.unlink(missing_ok=True)
    compression = release.get("derived_compression")
    if compression not in (None, "gzip"):
        raise ValueError("derived_compression must be gzip or absent")
    derived = directory / (stem + (".jsonl.gz" if compression else ".jsonl"))
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".derived-")
    temp = Path(temp_name)
    count = 0
    original_headers = None
    try:
        with contextlib.ExitStack() as stack:
            binary = stack.enter_context(os.fdopen(fd, "wb"))
            if compression:
                # No filename and mtime 0 in the header: identical input gives identical bytes.
                binary = stack.enter_context(
                    gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0)
                )
            output = stack.enter_context(
                io.TextIOWrapper(binary, encoding="utf-8", newline="\n")
            )
            for number, payload in iter_rows(original, release):
                count += 1
                if original_headers is None:
                    original_headers = list(payload)
                envelope = {
                    "source_release": period,
                    "source_sha256": sha,
                    "source_url": release["url"],
                    "source_row_number": number,
                    "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                }
                output.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
        if count == 0:
            raise ValueError("Empty source release is not accepted")
        install(temp, derived)
    finally:
        temp.unlink(missing_ok=True)
    metadata = {
        "id": release["id"],
        "source": source,
        "release": period,
        "sha256": sha,
        "table": table,
        "source_rows": count,
        "original_path": str(original),
        "path": str(derived),
        "format": "jsonl",
        "original_format": release["format"],
        "reader": {
            k: release[k]
            for k in ["format", "zip_member", "sheet", "header_row", "encoding", "formula_text_cells"]
            if k in release
        },
        "url": release["url"],
        "derived_sha256": sha256_file(derived),
        **({"derived_compression": compression} if compression else {}),
        "original_headers": original_headers,
        "conversion": "SponsorAtlas envelope v1; all values strings; blank records skipped",
        "http": http or {"fixture": True},
    }
    sidecar = directory / (stem + ".provenance.json")
    # First retrieval wins. Repeated downloads never rewrite prior provenance.
    try:
        with sidecar.open("x", encoding="utf-8") as output:
            json.dump(metadata, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except FileExistsError:
        existing = json.loads(sidecar.read_text(encoding="utf-8"))
        if any(
            existing[k] != metadata[k]
            for k in ["url", "sha256", "derived_sha256", "reader", "source_rows", "table"]
        ):
            raise ValueError("Immutable provenance differs") from None
        metadata = existing
    return metadata


def terraform_files(registry, base_url, root):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("Archive base URL must be HTTPS without query/fragment")
    result = {}
    for entry in registry:
        relative = Path(entry["path"]).resolve().relative_to(Path(root).resolve())
        result[entry["id"]] = {
            "schema": entry["source"],
            "stream": entry["table"].split(".")[-1],
            "url": base_url.rstrip("/") + "/" + urllib.parse.quote(str(relative)),
        }
    return {"file_releases": result}


def stage_text(destination, content):
    """Complete and flush one candidate without changing the published path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.pending-"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(content, encoding="utf-8")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def publish_outputs(outputs):
    """Stage the full pair, then roll back all paths if any publication fails.

    Replacements are individually atomic. This is rollback on caught errors, not
    a filesystem-wide transaction across power loss or concurrent external writers.
    """
    paths = [path.resolve() for path, _ in outputs]
    if len(paths) != len(set(paths)):
        raise ValueError("Registry and tfvars must use different output paths")
    staged = {}
    previous = {}
    attempted = []
    restored = True
    try:
        for (_, content), path in zip(outputs, paths, strict=True):
            staged[path] = stage_text(path, content)
        # Prepare every rollback image before replacing either published file.
        for path in paths:
            if path.exists():
                descriptor, name = tempfile.mkstemp(
                    dir=path.parent, prefix=f".{path.name}.previous-"
                )
                os.close(descriptor)
                backup = Path(name)
                previous[path] = backup
                shutil.copy2(path, backup)
            else:
                previous[path] = None
        for path in paths:
            attempted.append(path)
            os.replace(staged[path], path)
    except BaseException:
        try:
            for path in reversed(attempted):
                if previous[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(previous[path], path)
        except OSError as error:
            # Keep rollback images for recovery if the filesystem also refuses undo.
            restored = False
            raise RuntimeError(
                "Output rollback failed; preserve .previous files for recovery"
            ) from error
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        if restored:
            for backup in previous.values():
                if backup is not None:
                    backup.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("releases", type=Path)
    parser.add_argument("--release", action="append", help="Release id; repeat to select several")
    parser.add_argument("--archive-root", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--registry", type=Path, default=Path(".tools/ingestion/registry.prepared.json")
    )
    parser.add_argument(
        "--base-url", help="Reachable existing HTTPS archive origin; does not publish"
    )
    parser.add_argument("--tfvars", type=Path, default=Path(".tools/ingestion/files.tfvars.json"))
    parser.add_argument(
        "--local-file",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Archive a file already fetched with curl -fsSL from the release URL",
    )
    args = parser.parse_args(argv)
    try:
        releases = json.loads(args.releases.read_text(encoding="utf-8"))["releases"]
        if len({x["id"] for x in releases}) != len(releases):
            raise ValueError("Duplicate release ids would overwrite file source configuration")
        if args.base_url:
            terraform_files([], args.base_url, args.archive_root)
        local = dict(item.split("=", 1) for item in args.local_file)
        if local and not args.release:
            args.release = list(local)
        selected = [x for x in releases if not args.release or x["id"] in args.release]
        if not selected or args.release and set(args.release) - {x["id"] for x in selected}:
            raise ValueError("Unknown/empty release selection")
        registry = []
        failed = False
        for release in selected:
            try:
                if release["id"] in local:
                    official_url(release["source"], release["url"])
                    path = Path(local[release["id"]])
                    http = {
                        "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                        "final_url": release["url"],
                        "fetched_by": "curl -fsSL",
                    }
                    metadata = archive_release(release, path, args.archive_root, http)
                else:
                    path, http = download_release(release, args.archive_root / ".downloads")
                    try:
                        metadata = archive_release(release, path, args.archive_root, http)
                    finally:
                        path.unlink(missing_ok=True)
                registry.append(metadata)
                print(f"{release['id']}: {metadata['source_rows']} archived rows")
            except Exception as exc:  # noqa: BLE001 - CLI boundary returns documented error exit code
                failed = True
                print(f"{release['id']}: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        if failed:
            print(
                "ERROR: selection incomplete; existing registry/tfvars preserved", file=sys.stderr
            )
            return 2
        # A partial --release refresh must not delete previously prepared file sources.
        previous = json.loads(args.registry.read_text()) if args.registry.exists() else []
        merged = {item["id"]: item for item in previous}
        merged.update({item["id"]: item for item in registry})
        registry = [merged[key] for key in sorted(merged)]
        # Generate/validate both complete documents before touching either output.
        outputs = [(args.registry, json.dumps(registry, ensure_ascii=False, indent=2) + "\n")]
        if args.base_url:
            configuration = terraform_files(registry, args.base_url, args.archive_root)
            outputs.append((args.tfvars, json.dumps(configuration, indent=2) + "\n"))
        publish_outputs(outputs)
        return 2 if failed else 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns documented error exit code
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
