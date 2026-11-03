"""One original-lineage hash chain used before training, copying and evaluation."""

import json
from pathlib import Path

from er.io import digest


def validate_input(input_dir, *, training=None, prediction=None):
    inp = Path(input_dir)
    metadata = json.loads((inp / "input.json").read_text())
    for stem in ("records", "occurrences"):
        expected = metadata.get(stem + "_sha256")
        if not expected or digest(inp / (stem + ".parquet")) != expected:
            raise ValueError(f"input {stem} digest mismatch")
        for kind, manifest in [("model", training), ("prediction", prediction)]:
            if manifest is not None and manifest.get("input_" + stem + "_sha256") != expected:
                raise ValueError(f"{kind}/{stem} lineage digest mismatch")
    return metadata
