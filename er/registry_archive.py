"""Archive of published employer registries, so a rebuild keeps the employer IDs.

The backfill's er_predict step runs this instead of `er.predict`: it predicts with the
archive's latest registry as parent (INHERIT), then adds the new registry to the archive.
Merges, splits and retractions still stop with IDENTITY_REVIEW_REQUIRED (exit 3). New
employer IDs need an explicit --initialize-registry. Archived files are named by their
SHA-256 and never rewritten; LATEST names the newest one.
"""

import argparse
import json
import os
import shutil
from pathlib import Path

from er.io import digest
from er.predict import predict
from er.registry import IdentityReviewRequired, load_registry


def _checked(path):
    if not path.is_file() or digest(path) != path.stem:
        raise ValueError(f"archived registry {path} does not match its name")
    return path


def latest(archive):
    """The newest archived registry, or None when nothing was archived yet."""
    pointer = Path(archive) / "LATEST"
    if not pointer.exists():
        return None
    path = _checked(Path(archive) / pointer.read_text().strip())
    load_registry(path)
    return path


def publish(registry, archive):
    load_registry(registry)
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{digest(registry)}.json"
    if target.exists():
        _checked(target)
    else:
        partial = archive / f".{target.name}.partial"
        shutil.copyfile(registry, partial)
        os.replace(partial, target)
    pointer = archive / ".LATEST.partial"
    pointer.write_text(target.name + "\n")
    os.replace(pointer, archive / "LATEST")
    return target


def run(input_dir, model_dir, output, archive, *, initialize_registry=False):
    previous = None if initialize_registry else latest(archive)
    if previous is None and not initialize_registry:
        raise ValueError(
            f"no archived registry in {archive}; pass --initialize-registry to start new employer IDs"
        )
    summary = predict(
        input_dir,
        model_dir,
        output,
        initialize_registry=initialize_registry,
        previous_registry=previous,
    )
    publish(Path(output) / "registry.json", archive)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--archive", required=True)
    p.add_argument(
        "--initialize-registry", action="store_true", help="start new employer IDs instead"
    )
    a = p.parse_args()
    try:
        result = run(
            a.input, a.model, a.output, a.archive, initialize_registry=a.initialize_registry
        )
    except IdentityReviewRequired as exc:
        print(json.dumps({"status": exc.status, "error": str(exc), "details": exc.details}))
        raise SystemExit(3) from exc
    print(json.dumps(result))


if __name__ == "__main__":
    main()
