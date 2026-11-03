# Task 9 — Airflow 编排、24h freshness、告警与回填证据

## FY2025 backfill, facts layer (2026-10-28 UTC, phase 5)

The user raised the disk share to 36 GB (internal resource budget, 2026-10-27); the guard now reads
`scripts/project_limits.DISK_SHARE_BYTES`. The two remaining tasks of `sponsoratlas_backfill`
ran with `orchestration/run.sh airflow tasks test sponsoratlas_backfill <task>` (the earlier steps
had finished and refuse to overwrite their outputs). 32 cores, load average 6.7–8.0; other
projects were running.

| Step | Seconds | Result |
|---|---:|---|
| fact_inputs | 29.2 | projected 20.72 GB < 36 GB; FACTS_INPUT.lca 717,449 rows |
| dbt_facts | 42.2 | 48/48 PASS (`task09-backfill/dbt_facts_run_results.json`) |

`.tools/facts/fy2025-full.duckdb` is 2.63 GB; the project is at about 20.1 GB. `fact_lca_case`:
**713,467** latest-status cases (one per case number): FY2024 Q4 118,646, FY2025 594,821
(Q1 105,683, Q2 132,133, Q3 238,425, Q4 118,580). Content checks from `scripts.fact_audit`:
0 winner mismatches, 0 newer decisions left out, 0 BLS wage mismatches
([fact-summary.json](../../evidence/task09-backfill/fact-summary.json)). The full-year DBs are now the canonical
paths (`scripts/canonical_paths.py`); Cube was re-exported from them
(task05-06-canonical). Logs: `task09-backfill/airflow-tasks-test-*.log`,
`task09-backfill/logs/{fact_inputs,dbt_facts}.log`.

## FY2025 full-fiscal-year backfill (2026-10-28 UTC, phase 4)

Raw outputs: [`task09-backfill/`](../../evidence/task09-backfill/) (`summary.json`, one JSON + log per step,
`readiness.json`, `staging-summary.json`, three `airflow-dags-test*.log`, `archive.log`).

- **Inputs.** DOL served FY2025 Q1–Q3 (`curl -fsSL` from www.dol.gov, no 403 this time).
  DOL publishes no checksums; the SHA-256 is pinned on first retrieval in `ingestion/releases.json`
  and `dbt/source_contracts/verified_releases.json`. Archived with gzip JSONL (deterministic:
  re-archiving gave identical derived SHAs):

  | Release | Rows | Decision dates | XLSX | JSONL.gz |
  |---|---:|---|---:|---:|
  | FY2025_Q1 | 107,414 | 2024-10-01 – 2024-12-31 | 87.7 MB | 37.5 MB |
  | FY2025_Q2 | 132,133 | 2025-01-01 – 2025-03-31 | 107.0 MB | 46.1 MB |
  | FY2025_Q3 | 238,425 | 2025-04-01 – 2025-06-30 | 143.9 MB | 84.5 MB |
  | FY2025_Q4 (existing) | 118,580 | 2025-07-01 – 2025-09-30 | 79.1 MB | — |

  Source drift found by the gates: Q1 spells `H-1B_DEPENDENT` (Q2–Q4: `H_1B_DEPENDENT`) and lacks
  `LAWFIRM_BUSINESS_FEIN`. A mapping row was added; both columns are excluded from staging.
  Q2 stores every text cell as a `="…"` string-literal formula; the reader takes exactly that form
  as its text. One cell (row 109,308, `PW_SURVEY_PUBLISHER` = `=- AON, Radford Mclagan
  Compensation _xlnm.Database`) is free text stored as a broken formula. It is listed in the release
  config and kept verbatim. Any other formula still fails the archive.
- **Run.** New manual DAG `sponsoratlas_backfill` (one task per step, retries 0 because each step
  refuses to overwrite its destination), run with `orchestration/run.sh airflow dags test
  sponsoratlas_backfill`. Machine: 32 cores, load average 5.4–7.6; other projects (PathWatch, Keel)
  were running.

  | Step | Seconds | Result |
  |---|---:|---|
  | load_raw (RAW load of 5 LCA + 2 USCIS files) | 15.6 (run 2) | 810,764 rows; all 7 tables reconcile with archive counts (run 3: 10.5 s reconcile) |
  | dbt seed / staging build | 4.1 / 76.7 | 43/44 pass; `staging_flag_limits` fails on USCIS FY2023 only |
  | ER export / train / predict | 4.1 / 8.5 / 10.2 | 164,059 source records; u/EM (no human labels); 0.9 threshold unchanged; 105,379 LINKED |
  | dimension inputs / dbt dimensions | 6.1 / 5.8 | 98,343 employers, 7,042 geographies, SCD2 + reference gates pass |
  | fact_inputs | — | **refused by the disk-share guard**: projected 20.72 GB > 18 GB |

  DagRun run 3: 138.0 s, state failed at `fact_inputs`. Runs 1 and 2 failed on two loader bugs,
  both fixed with tests. The loader read the BLS sidecar too. Its summary path was missing after the
  DB had committed; that step now reconciles an existing DB instead of reloading it. The failure
  callback ran but had no alert destination configured locally (`SPONSORATLAS_ALERT_TRANSPORT`);
  the HTTP-sink alert drill is the 2026-10-24 one below.
- **Staging on the full year.** FY2025 `geo_unresolved` = 6,716 / 596,552 = **1.13%** (limit 5%
  unchanged). Q1–Q3 have 48 / 71 / 108 unresolved rows; Q4 alone keeps 6,489 (5.47%).
  FY2024 0.05%. USCIS FY2023 `state_unresolved` 7.44% still fails (user decision).
- **Not done.** Facts were not rebuilt for the full year: the new DBs (staging 2.07 GB, ER 0.36 GB,
  dimensions 0.09 GB) put the project at 17.42 GB. A full-year facts DB is estimated at 1.6 × staging
  (~3.3 GB), and deleting the superseded Q4-only DBs (~2.9 GB) needs `rm`, which needs approval.
  The site, Cube and OpenMetadata still read the Q4-only canonical DBs.

## 2026-10-24 local orchestration

截至 2026-10-24 UTC，本地编排机制已实现并实跑；**真实 Airbyte/Snowflake、外部 Slack/邮件实收、整财年回填仍 OPEN**。下列合成 HTTP 接口、受控小型建模数据库和只读跨驱动实验均不是上述远端验收的替代。

## 实现与部署边界

DAG 位于 `orchestration/dags/sponsoratlas.py`，固定 Airflow3.3.2、Cosmos1.15.1、standard-provider1.19.0，隔离依赖锁见 [`requirements.lock`](../../../orchestration/requirements.lock) / 版本及约束 SHA。根 dbt/Splink/锁与既有40个源未改。导入阶段仅 native dbt ls，不请求服务；无凭证生产导入得到35任务、无环、全部 retries=2。

路径为 `airbyte_sync → jobs_source_freshness → Cosmos dbt_public（31个原生 build）→ public_quality_gates → cube_refresh`。完整 task ID、上下游、operator、select 在 图清单。每日08:00UTC，catchup=false、max_active_runs=1、公共DAG max_active_tasks=1；每任务15分钟硬限、两次重试。私有v2应用模型默认禁用；保留既有 legacy CDC metadata staging 测试，但公开投影严格只读取三个公共事实及其公开维度，不导出应用、owner或PII字段。

Cosmos逐节点使用 `dbt build --indirect-selection cautious`，最后显式 `dbt test` 选择三个public tags和13个既有单项闸门，补齐多父测试。原生最终执行 **77 data tests + 1 unit test，78 PASS**，包括全部13个事实FK、六个旧stage门禁、grain/accounting/BLS/dimension/SCD2检查；三个公共事实 enforced contracts均为true。精确 test ID及depends_on见 77项选择，实际每个native任务/78项结果及合约见 dbt执行。数量本身不是覆盖证明。

生产入口及确切变量见 [`config.production.env.example`](../../../orchestration/config.production.env.example) 和 [运行手册](../../../orchestration/README.md)。缺 mode/endpoint/connection/token/告警目的地时失败，无静默fixture fallback。Airbyte连接应为Manual调度，已治理RAW、DIMENSIONS_INPUT和FACTS_INPUT映射须预先存在，本任务不杜撰ER、全FY输入或平台隔离。

## 原生调度与通知

DagRun/任务UUID及状态 保留了所有演练，包括失败和显式中止。

| 真实本机调度 | UTC开始 → 结束 | 结果与范围 |
|---|---|---|
| `sponsoratlas / task9_final_20261024` | 20:17:56.493358 → 20:22:11.247965 | **254.754607s，35/35成功，全部try1**；原生dbt、公共门禁、当时Cube REST路径成功。后续自审发现CLI闭库持久性缺口，修复证据另列，不能据此行独自验收持久性。 |
| `sponsoratlas / task9_stale_final_20261024` | 20:24:16.097531 → 20:25:03.645437 | greenhouse合成加载时间改为当前UTC减25h；freshness三次尝试后FAILED，Cube `upstream_failed`、try0，其余后续均未执行。 |
| `sponsoratlas_deadline_probe / task9_deadline_final_20261024` | 20:17:56.494458 → 20:18:24.800977 | 25s任务SUCCESS；五秒Deadline由scheduler生成/执行，**20:18:02.735572实收通知，发生在运行中**。 |

告警收件是 **127.0.0.1合成HTTP sink**，不是Slack/邮箱。失败task callback在20:24:58.613065收到，DAG callback在20:25:07.416123又收到；因此可见两个终态通知。完整 HTTP收件与sync触发记录 / freshness sources.json摘要。本次过期演练只触发一次合成sync job6，源新鲜度重试不重复触发sync。

采用维护版Airflow3.3.2的 `DeadlineAlert(DAGRUN_QUEUED_AT, 30min, SyncCallback)` 承接旧SLA回调意图；运行时排队参考避免历史logical_date使回填立即超时。其运行中检查与旧post-run SLA时点不同，且官方仍标experimental，故固定版本并做真实scheduler验证。[官方Deadline接口](https://airflow.apache.org/docs/apache-airflow/3.3.2/howto/deadline-alerts.html)。24h jobs freshness独立于运行时Deadline；加载字段 `_airbyte_extracted_at`，空表或未知加载时间失败关闭，未伪造真实历史观测/heartbeat。

## 重试与试用截止

Airbyte按endpoint/connection/run_id保存共享耐久状态，先排他写入trigger_pending再POST；已有job只resume，不新触发。POST响应丢失时要求核对真实job，不能删除state盲重试。840s轮询预算后预留60s，对**同一job** DELETE取消并确认；取消未确认耐久标记、告警且禁止下一次触发。取消接口依据 [Airbyte官方API](https://reference.airbyte.com/reference/canceljob)，真实平台尚未调用。

生产早期sync和每个原生dbt执行均校验明确UTC试用start/expiry（最多30天），保留Cube既有七分钟安全余量。专属dbt wrapper启动前重新计算 `min(840, configured_limit, cutoff-now-60)`，过近截止拒绝、超时以有界SIGINT/TERM/KILL清理，晚返回也不报成功；Snowflake原生connect额外固定DBT_SVC/TRANSFORMER/JWT/XS、statement240s、queue60s、abort-detached，根profile不变。Cube仍沿用CUBE_SVC/ANALYST、XS/60、只读allowlist及实际SDK派发截止。near-cutoff预算压缩、late-return、取消未确认/不重复POST均有聚焦测试。自审还发现试用余量检查会以RuntimeError跳过已启动job取消；先做RED，再改为进入同job取消/确认，聚焦GREEN。远端job取消/权限/试用账户实际设置仍OPEN。

## Cube物理刷新与自审修复

受控入口 `semantic/scripts/refresh.cjs` 与网关共享每work排他lease；运行网关或并发refresh会拒绝，SIGKILL遗留lease需核对确切owner后人工处理。未增加公共admin端点/路由或Snowflake写权限。可信owner-only状态使用严格32位小写hex revision、原子rename+fsync；失败构建不得promote。

每次维护用私有loopback Cube、顶层REST `cache: "must-revalidate"` 构建两个counts rollup，工资精确分位保持原逻辑。新revision改变失效键，解决同小时hourly key复用旧表的问题；网关重启读取同一成功revision，复用同一物理表。停机维护是明确代价。

自审的真实失败：原CLI的preflight `getDriver`和REST执行产生同文件两个独立可写DuckDB catalog；旧catalog最后释放覆盖了新rollup。当时REST usedPreAggregations=true，但闭库独立SQL为**0张BASE TABLE**。原 RED、闭库前0表/REST成功日志、原DagRun REST记录 保留。修复让CLI和网关共享source driver，闭库后独立READ_ONLY重开、验证两个新物理表及计数后才提交ready。

修复后未重启Airflow；定向CLI重跑证明：

- 原DagRun的合成公开快照：两张物理表持久，postings=2；新增合成观测经原生增量build+9 data tests（10/10成功）后，新快照postings=3。原两条UTC观测逐值未改。闭库Node核验与独立根DuckDB2只读复核结果见 修复后物理证据、增量输入、增量dbt结果。
- 真实CLI临时快照：闭库2表；同revision启动真实网关读取postings=4，关闭后表名/数据复用；活动网关时refresh退出失败且ready不变。专属Python测试未使用Node的force-exit：CLI和gateway等待shutdown/release后均退出0，gateway由SIGTERM正常收尾（KILL会使断言失败），闭库独立SQL逐表全部行及ready字节保持一致；定向1/1输出。29项Node套件使用force-exit，其退出本身不作自然排空证明。[CLI/网关重启证据](../../../orchestration/evidence/cube-cli-persistence.json)。
- tiny物理READ_ONLY源 → 独立external DuckDB：同小时4→5，旧revision保留4，新revision新物理表=5，同revision重启无CREATE/INSERT，故意构建后失败不promote且旧ready可恢复。[跨驱动证据](../../../orchestration/evidence/cube-external-refresh.json)。这是机制验证，**不是Snowflake实跑**。

本地新导出只允许orchestration-owned独立DB，校验维度/事实grain、投影计数、SHA manifest、128MiB/100万行上限；失败清理输出。既有六份公开Parquet与受保护1.7GB事实库均未改。既有Snowflake export路径未调用/修改，原deferred M1仍保留；新本地导出进口/失败清理与拒绝fanout已覆盖。

## 财年回填仍OPEN

执行 `.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill --fiscal-year 2024`，退出2：要求 `2023-10-01,2024-10-01)`，实际仅FY2024_Q4、120897行，覆盖2024-07-01～2024-09-30，Q1/Q2/Q3缺失；**无mutation/sync**。[预检及耗时。约0.045s是拒绝前置检查耗时，不是全FY回填耗时。

控制者同会话核对DOL：Q1–Q3仅候选，普通HTTP HEAD/GET前4096B均403，已验证Q4对照也403；不能据此说季度不存在，也不能认作正式发布。官方站可用性原始记录。无大文件下载。

后续最小入口：确认官方发布链接，归档原件+SHA+完整header/row/date范围，显式新增季度列/输入/source映射并审查；按实际大小预估新的有界运行库和分季载入空间，再执行全财年回填。保留现有库只读，不能复制1.7GB基线、重复Q4或把当天职位改写为历史日快照。当前不能提供真实整FY耗时。原始源质量、ER500人工标签、真实Free/Snowflake/Airbyte、Task8私有CDC/UI等门禁继续OPEN。

## 验证、资源与收尾

可重放命令在 [运行手册](../../../orchestration/README.md)。最终 Python 22/22、受影响既有source/lifecycle/external/七指标REST 29/29、两项新原生Cube测试2/2及 lint 全部通过后本地提交，未push/发布。

隔离环境最初约222MB；全部安装无缓存，未新增镜像/volume/build cache，未重装OM/Cube/Spark。早期终端退出留下项目Airflow子树，已精确所有权清理并保留 清理记录，没有停止另一项目Airflow。之后supervisor拥有完整树，RSS上限8GB、1200s，最终主调度峰值 **1,748,725,760B**、清理后live-owned=[]；早先含旧Cube尝试峰值2,457,358,336B，孤儿清理时项目子树合计3,267,915,776B，均低于16GB。采样/清理。

磁盘最终按 `max(路径字节含symlink, du实际分配且排除不可读PG)`，再加PG分配/原件较大值、既有镜像双计、写层及30MB Docker日志保守储备，见 最终审计，小于18e9。治理三服务仍全停；Airflow与合成收件sink已停、专属监听端口均关闭，进程证明 / 收件sink清理。所有瞬态native测试均闭连接、退出并清理临时目录。保留依赖、锁、私有runtime元数据与证据以便独审重放。

41项源/ER/维度/事实 SHA、bytes、mtime逐项完全相同；事实库仍1722822656B，SHA `c5c5b2ed0544146f2735682c5ab388930f5e7221c01a910df96561d1a4785e7a`。源保护。控制者另核对六份原公开actual/fixture Parquet的SHA/bytes/mtime与Task8一致（控制者六份补充）。无HOME改写、云创建、GPU或其它项目写入。Azure新增估算$0，已有实际账单未知；预算/STATUS由控制者维护。
