# Human employer labels

`pairs.csv` holds the human labels used to evaluate employer entity resolution: 559 pairs
labelled by one reviewer and imported on 2026-10-29 (243 hard cases; 454 train, 105 holdout).
`pairs.manifest.json` records the source workbook's SHA-256, the counts and every skipped pair.
Equal EINs, high match probabilities or a candidate's stratum never become labels automatically;
synthetic truth for the math and pipeline tests lives only in `er/tests/fixtures`.

## Format

One row per candidate pair:

| Column | Meaning |
|---|---|
| `left_id`, `right_id` | ER source-record IDs (`unique_id` in the ER input's `records.parquet`) |
| `left_source_system`, `left_source_employer_key`, `right_source_system`, `right_source_employer_key` | filled from the ER input by ID |
| `label` | `1` same employer, `0` different employers |
| `hard_case` | `1` or `0` |
| `label_method` | always `human` |
| `reviewer` | the reviewer's name |
| `reviewed_at` | ISO 8601 with a time zone; for the 2026-10-29 import this is the import time, because the workbook has no per-pair review time |
| `evidence` | per-pair evidence; the 2026-10-29 workbook left it empty, so it names the official source rows the reviewer was shown |
| `rationale` | the reviewer's reason |
| `split` | `train` or `holdout`, taken from the frozen candidate list |

`er.evaluate` refuses labels that break these rules: at least 500 human pairs and 100 hard
cases, binary `label`/`hard_case`, a time zone on `reviewed_at`, no duplicate or self pairs, and
no record in both splits. The frozen split (`er.label_workflow`) assigns whole connected
components of the candidate graph by a fixed hash (seed 42, about 20% holdout).

## Import a returned workbook

The workbook has a `pairs` sheet (`pair_id, left_id, right_id, split, label, hard_case,
rationale, evidence`) and an instructions sheet `说明` whose `标注人` (annotator) cell is followed
by the reviewer's name. A CSV export of the `pairs` sheet also works with `--reviewer <name>`.

```bash
.venv/bin/python -m er.label_import --source <workbook.xlsx> \
  --input .tools/er/fy2025-backfill/input --frozen .tools/er/review-candidates-v2.csv \
  --output er/labels/pairs.csv [--evidence-from-source-rows]
# Optional: how many frozen pairs still map to the current ER input.
.venv/bin/python -m er.label_alignment --input .tools/er/fy2025-backfill/input \
  --labels .tools/er/review-candidates-v2.csv --output <report.json>
```

Rows with an empty label are skipped and counted; rows whose endpoint is no longer in the ER
input are skipped and listed in the manifest (41 on 2026-10-29: one endpoint was a USCIS row
without an address, excluded from the release). An invalid labelled row stops the import and
nothing is written. An existing `pairs.csv` that already holds labels is never overwritten.
`--evidence-from-source-rows` fills an empty evidence cell with the source rows the workbook showed.

## Train and evaluate with the labels

```bash
.venv/bin/python -m er.train --input .tools/er/fy2025-backfill/input \
  --labels er/labels/pairs.csv --output <model-dir>          # u/EM excludes all holdout endpoints
.venv/bin/python -m er.predict --input .tools/er/fy2025-backfill/input --model <model-dir> \
  --output <prediction-dir> --previous-registry <archived registry.json>
.venv/bin/python -m er.evaluate --input .tools/er/fy2025-backfill/input --model <model-dir> \
  --predictions <prediction-dir> --labels er/labels/pairs.csv --output <report.json>
```

`er.evaluate` scores the holdout on the final clusters at `er.settings.THRESHOLD` and exits 2
when labels are missing or invalid, 1 when precision < 0.97 or recall < 0.90, and 0 otherwise.
The same check as an opt-in test:

```bash
SPONSORATLAS_ER_ACCEPTANCE=1 \
SPONSORATLAS_ER_ACCEPTANCE_INPUT=... SPONSORATLAS_ER_ACCEPTANCE_MODEL=... \
SPONSORATLAS_ER_ACCEPTANCE_PREDICTIONS=... SPONSORATLAS_ER_ACCEPTANCE_LABELS="$PWD/er/labels/pairs.csv" \
SPONSORATLAS_ER_ACCEPTANCE_OUTPUT=... \
.venv/bin/python -m pytest er/tests/test_evaluate.py::test_evaluate_meets_thresholds -q
```

Result of the 2026-10-29 run: [`docs/evidence/task04-human-acceptance-20261029/`](../../docs/evidence/task04-human-acceptance-20261029/).
There is one reviewer and no second review.
