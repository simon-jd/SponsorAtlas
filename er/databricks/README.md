# Databricks Free Edition path (not run)

Code for running the same Splink 5 model on Spark instead of DuckDB. It has never run on
Databricks; only the local protocol tests and one local Spark Connect run on 96 synthetic
records exist.

- `notebook.py`: serverless notebook entry. It checks the archived ER input by file hash, runs
  Spark random-u sampling (seed 42), EM and predict, and keeps intermediate results in Delta
  tables owned by the run. Clustering with the strong-ID constraints runs in the Python driver
  (bounded to 250,000 source records and 5 million candidate pairs).
- `job.json`: serverless job template (timeout 1800 s, one concurrent run, no retries); paths are
  placeholders and no job has been created.
- `er/spark_connect.py`: a `SparkConnectAPI` extension of Splink's database API, because Free
  Edition offers only Spark Connect.

Prerequisites: a Free Edition workspace with `analytics.er_scratch` and `analytics.intermediate`
schemas, a writable Unity Catalog volume holding the ER input (`records.parquet`,
`occurrences.parquet`, `input.json`), and the project source in the workspace. The notebook
installs Splink 5.0.0 and PyArrow 26.0.0 and keeps the bundled Connect client.

Local check (needs a local Spark Connect server, opt-in):

```bash
SPONSORATLAS_SPARK_CONNECT_TEST=1 .venv/bin/python -m pytest er/tests/test_spark_live.py -q
```
