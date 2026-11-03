"""Read-only public board smoke check and SHA archive; does not sync to Snowflake."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def probe(provider, board, root):
    if provider not in {"greenhouse", "lever", "ashby"} or not re.fullmatch(
        r"[A-Za-z0-9_-]+", board
    ):
        raise ValueError("Unsupported provider or invalid public board token")
    moment = datetime.now(UTC)
    fiscal_year = moment.year + int(moment.month >= 10)
    fiscal_quarter = ((moment.month - 10) % 12) // 3 + 1
    release = f"FY{fiscal_year}_Q{fiscal_quarter}"
    origin = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
        "lever": f"https://api.lever.co/v0/postings/{board}?mode=json",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
    }[provider]
    total = offset = 0
    pages = []
    directory = Path(root) / "jobs" / release
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        url = origin + (f"&skip={offset}&limit=100" if provider == "lever" else "")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "SponsorAtlas/0.1 public board probe",
            },
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read(50 * 1024**2)
            metadata = {
                "url": response.url,
                "http_status": response.status,
                "observed_at_utc": datetime.now(UTC).isoformat(),
            }
        payload = json.loads(body)
        records = payload if provider == "lever" else payload["jobs"]
        if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
            raise ValueError("Unexpected public board response shape")
        if provider == "greenhouse" and payload["meta"]["total"] != len(records):
            raise ValueError("Greenhouse total differs from returned jobs")
        sha = hashlib.sha256(body).hexdigest()
        path = directory / f"{provider}_{board}_{release}_p{offset}__{sha}.json"
        if path.exists():
            if path.read_bytes() != body:
                raise ValueError("Immutable archive content changed")
        else:
            with path.open("xb") as output:
                output.write(body)
        pages.append({**metadata, "sha256": sha, "path": str(path.resolve()), "rows": len(records)})
        total += len(records)
        if provider != "lever" or len(records) < 100:
            break
        offset += len(records)
        if offset > 100000:
            raise ValueError("Public board exceeded pagination safety limit")
    return {
        "provider": provider,
        "board": board,
        "status": "PUBLIC_GET_ONLY",
        "release": release,
        "rows": total,
        "pages": pages,
        "snowflake_sync": "UNVERIFIED",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["greenhouse", "lever", "ashby"])
    parser.add_argument("board")
    parser.add_argument("--archive-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args(argv)
    print(json.dumps(probe(args.provider, args.board, args.archive_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
