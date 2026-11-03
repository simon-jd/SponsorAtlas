# Task 2 抽取证据（2026-10-23）

## 2026-10-28: Airbyte OSS via abctl — blocked by the host inotify limit

Raw outputs: `task02-airbyte/`.
abctl v0.30.4 linux-amd64 from the GitHub release. The SHA-256 matches the published
`abctl_0.30.4_checksums.txt` (and the 2026-10-23 copy). Run with `make airbyte-up`:
chart 1.8.5, `ingestion/deploy/airbyte-values.yaml`, `--low-resource-mode`, port 18000,
`DO_NOT_TRACK=1`. abctl has no option for the cluster name (`airbyte-abctl`) or the state dir
(`~/.airbyte`).

- Attempt 1 (default chart, stopped): kind created the node, then the API server refused
  connections while removing the control-plane taint; kind deleted the node.
- Attempt 2: the cluster and the chart came up, `airbyte-db-0` ran, and the bootloader failed after
  3 m 9 s. Its call to the Kubernetes service 10.96.0.1:443 timed out, because kube-proxy was in
  CrashLoopBackOff with `failed complete: too many open files`. That is the host's inotify instance
  limit (about 120 of 128 in use by other projects). Raising it needs root.
- Resource use at failure: node container memory 2.13 GB, peak 4.00 GB (share 16 GB);
  `~/.airbyte` 5.8 MB readable plus a Postgres volume owned by the container user.
  32 cores, load average about 7, other projects running.
- `make airbyte-down` removed the cluster. `ingestion/*.tf` was not applied (no running Airbyte).
  The pgoutput CDC drill below remains the working CDC path.

## 2026-10-27：本地 Postgres CDC 实跑（当前结果）

命令：`make cdc-drill`（项目自建容器 `sponsoratlas-cdc-pg`，postgres:16-alpine，`wal_level=logical`，
用 `ingestion/postgres/{demo_schema,configure_cdc}.sql` 建表、publication 和 pgoutput slot；跑完自动删除容器）。
消费端 `scripts/cdc_pgoutput.py` 自己解码 pgoutput v1，用 `pg_logical_slot_peek_binary_changes` 每 30 秒拉一次，
写入 DuckDB `RAW_JOBS.CDC_JOB_POSTINGS / CDC_APPLICATION_EVENTS`（Airbyte 去重表的形状，删除保留为 tombstone），
DuckDB 提交后才 `pg_replication_slot_advance`。数据全部是合成的。原始输出：[task02-cdc/](../../evidence/task02-cdc/)。

| 变更 | 表 | commit → RAW 秒 |
|---|---|---:|
| insert / update / delete | job_postings | 29.43 / 29.42 / 29.42 |
| insert / update / delete | application_events | 29.41 / 29.41 / 29.41 |

6 个变更全部在 15 分钟内进入 RAW（最大 29.43 s，主要是 30 s 拉取间隔）。PostgreSQL 16.15，slot 插件 pgoutput。
种子 200 + 80 行；对账：Postgres 200/80 = RAW 当前行 200/80，两表各 1 个 tombstone。

`scripts/reconcile_counts.py --duckdb` 对本地 RAW 副本逐文件对账，全部 MATCH：
LCA FY2024_Q4 120897、FY2025_Q4 118580，USCIS FY2022 59983、FY2023 33332（`.tools/staging/actual-files.duckdb`），
BLS May2025 413527（`.tools/facts/actual.duckdb`）。board API 的 86 条职位快照没有纳入这次对账。

Airbyte（abctl）未安装：项目当前占用 13,225,795,339 字节（不含一个容器自有、无读权限的 PG 数据目录），
距 18 GB 份额只剩约 4.8 GB，装不下 kind 节点加 Airbyte 平台镜像，所以按计划保留 pgoutput 路径。
目标仍是本地 DuckDB，不是 Snowflake RAW（P9）。

**结论：本地实现与测试通过；原计划真实同步、全部文件行数相等、Postgres 增改删 15 分钟进入 RAW 仍未验收。** 未安装运行 Airbyte、未申请 VM/付费资源、未执行 Terraform apply、未写 Snowflake/Postgres、未发布或 push。

## 版本、配置和接口

Python 3.12.13；Terraform 1.13.5；`airbytehq/airbyte=1.3.0`（provider lock 已提交）；file image 0.6.0、Postgres 3.8.9、Snowflake 5.2.0；CDK 7.39.1；openpyxl 3.1.5；PyYAML 6.0.3；psycopg 3.3.6。本地 recipe 为 upstream abctl 0.30.4（固定 Linux archive SHA256）、chart/platform 1.8.5。只完成 wrapper dry-run 和 shell syntax 检查，上游 abctl硬编码账号home下`~/.airbyte`；当前主机home在项目外，wrapper显式拒绝项目外host-home执行，仅检查Airbyte/kind固定名称和8000端口冲突，不因其他项目container存在而拒绝。需先有项目内正常账号home、受控Docker访问及资源限额的隔离controller；没有patched fork、没有HOME改写。没有声称这些镜像/Builder runtime 已在目标本地平台完成端到端兼容验证。

代码：`ingestion/main.tf,sources.tf,connections.tf`、`board_api/manifest.yaml`、`postgres/`、`deploy/`；CLI 和测试在 `scripts/`。完整操作说明：[ingestion README](../../../ingestion/README.md)。依赖和 lock 由父任务更新。最新预算要求：Airbyte/Airflow/OpenMetadata/Cube 在 home-ubuntu 本地；Snowflake30天试用等待用户注册，凭证前用本地原件/fixture，后续建模用DuckDB；Databricks Free Edition。SponsorAtlas≤18GB新增磁盘、≤16GB常驻服务总RAM；Azure≤$6，开通前台账估算累计≤$5.40，只允许 `rg-sponsoratlas-lab` 和规定四tags，不触碰 `rg-mlinfra-westus`。本任务无云资源开通。当前项目约656MB、可用RAM约85GiB，读取到共享Docker有20余个其他项目container，未更改或清理它们。

Task1 `RAW.LCA/USCIS/BLS/JOBS`、`AIRBYTE_SVC/LOADER`、`SPONSORATLAS_XS` 保持一致；不创建 warehouse，XS/auto_suspend=60 与服务 key-pair 约束保持。

每 release 的稳定 RAW 表如 `RAW.LCA.LCA_FY2024_Q4`；列为 `SOURCE_RELEASE,SOURCE_SHA256,SOURCE_URL,SOURCE_ROW_NUMBER,PAYLOAD_JSON`。最后一列是保留所有原始 header 的 JSON 字符串；Task3 应检查未映射原始键。board `acme_gh` connection 输出 `RAW.JOBS.ACME_GH_GREENHOUSE` 等确定性前缀；CDC 是 `RAW.JOBS.CDC_JOB_POSTINGS`、`RAW.JOBS.CDC_APPLICATION_EVENTS`，逻辑 slot 为 pgoutput，删除保留 tombstone。

## TDD 与本地验证

RED：先写 `test_reconcile_flags_mismatch` 和 invalid CLI case，再运行 `.venv/bin/python -m pytest scripts/tests/test_reconcile_counts.py -q`；因 `scripts.reconcile_counts` 不存在而 collection error。日志 `.tools/ingestion/tdd-red.log`。这是明确的未实现失败，不是真实服务失败。

初版 `bf54536` GREEN：`.venv/bin/python -m pytest -q` → **34 passed, 3 skipped**，ingestion 27 passed；历史日志 `.tools/ingestion/tdd-green.log`。

审查修复 round1（working base `83f1de3`，父任务后续文档提交 `1338a16`）：先添加回归，聚焦运行 CDC/archive 测试得到 **4 failed, 18 passed**，真实复现 final job lookup 返回903s仍PASS、tfvars生成/写入失败污染registry、缺少第二发布失败恢复；RED日志 `.tools/ingestion/task2-fix1-red.log`。修复后同一回归及相关CDK契约聚焦 **34 passed**；最终仅运行一次全套 `.venv/bin/python -m pytest -q` → **95 passed, 3 skipped**，包含ingestion39、既有warehouse7和父任务Azure预算49个本地测试；3个真实Snowflake grants tests保持skip。最新日志 `.tools/ingestion/task2-fix1-green.log`。CDK固定版本导入的3条 `ExperimentalClassWarning` 在相关测试fixture中精确断言category/message/数量，不设全局ignore，未出现未捕获warnings。

覆盖：源/RAW mismatch；CLI 0/1/2 子进程退出码；CSV 多行与重复/空/不等宽列；ZIP/XLSX；新 headers/前导零保留；公式拒绝；原件/派生 hash、不可变归档、失败下载不覆盖已有 registry；SQL identifiers 参数化；真实 CDK 执行 Greenhouse/Ashby response shape、Lever 100+1 分页、空列表、异常 response shape；CDC probe 两表三操作控制流、超时、默认不连接。以上数据均为标明人工 fixture，不是官方文件行数或真实 CDC 证据。

`.tools/terraform/terraform -chdir=ingestion init -backend=false -input=false`、`fmt -check`、`validate` 全通过；`terraform test -no-color` → **1 mocked plan passed**。真实 provider 1.3.0 schema 已验证，无 Airbyte API 调用。日志 `.tools/ingestion/terraform-init.log,terraform-test.log`。Ruff check/format、`bash -n ingestion/deploy/deploy_airbyte.sh`、wrapper `--dry-run` 通过。未在共享 Docker 上运行 abctl。

## round1 修复行为与边界

CDC probe 共用单一绝对deadline，在 mutation、trigger、fetch、job status、wait 返回后及最终PASS前重新检查；最终 `total_seconds` 直接使用已检查的同一个时钟样本，不能报告903/900通过。Airbyte token与job请求分别使用递减剩余预算（最多30s），响应读取后仍检查；Postgres每条业务SQL/COMMIT前更新剩余预算的statement_timeout，并设置30s连接/初始statement/lock上限；Snowflake每条查询传递剩余整数秒timeout，不足1秒直接失败，避免timeout=0禁用取消。驱动返回晚于总截止时强制FAIL；这些均为人工时钟/假连接回归，没有真实服务延迟结论。

registry/tfvars先完整生成校验，再写同目录临时文件并flush，保留两份旧文件的恢复副本后才逐个rename发布。生成失败、暂存写失败、第二文件发布失败均保持或恢复原组合；首次发布失败不遗留单边文件。成功路径校验两份输出引用同一归档SHA。恢复针对可捕获的进程错误，不宣称两条路径在断电或外部并发编辑下为跨文件原子事务；若文件系统同时拒绝恢复，会保留 `.previous-*` 供人工恢复并退出错误，不报成功。

本轮Ruff check/format、`git diff --check`通过。Terraform/部署文件未变，沿用上次真实provider schema/离线mock plan证据，不为Python修复重复运行无关部署检查。预算、清理helper、reaper、Makefile、根文档未由本轮修改。没有创建资源、apply、同步或push；原始真实验收缺口全部保留。

## 真实公开源 GET（与 RAW 验收分开）

普通官方 endpoint 下载尝试，日志 `.tools/ingestion/public-download.log`：

| 来源/发布期 | 结果 | 状态 |
| --- | --- | --- |
| DOL FY2024 Q4、FY2025 Q4 XLSX | HTTP 403 | 无原件、无真实源行数 |
| BLS May2024、May2025 all-data ZIP | HTTP 403 | 无原件，ZIP member/布局未实物确认 |
| USCIS FY2024、FY2025 候选年度 CSV | HTTP 404 | 精确链接未确认，条目已禁用 `requires_official_release_url` |

USCIS 尝试的官方候选是 `https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2024.csv` 和 `...-2025.csv`；不将它们作为可用 verified defaults。官方文件页在先前只读研究返回 403，下一步需从官方发布页确认真正的年度 CSV 地址；没有使用第三方镜像，也没有编造 counts。`ingestion/releases.json` 明确标记各未验证项。BLS 财年目录只是归档标识，数据统计期为 May calendar year。

公开 board 检查命令为 `python -m scripts.public_board_probe <provider> <board>`；均 GET 200，原始响应保存于 `data/raw/jobs/FY2027_Q1/`（2026-10 的美国财政分期，未提交数据）：

| API / board | 返回 postings | 原始 HTTP body SHA256 |
| --- | ---: | --- |
| Greenhouse / greenhouse | 19（与 meta.total 一致） | `1d7dc90baec17a3ece95d6aaff643c2b74d056956932e58eab7dfac8768f49bd` |
| Lever / lever | 0（有效空列表） | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| Ashby / Ashby | 67 | `8f45310e1d62ffe0a3cd4d05dff60d425c804e128dfa7d264f1a3028624562b9` |

抓取 URL、UTC 时间、SHA、路径和数量分别在 `.tools/ingestion/{greenhouse,lever,ashby}-public.json`；Lever 实际空响应只能证明公开端点可读，多页行为由人工 100+1 CDK contract 测试验证。以上均未写 Snowflake，不能计入“各源真实同步成功”。board 数据会变化，不能把这些时点 counts 当作永久断言。

## 自审与剩余外部门槛

自审修正：对账默认 warehouse 与 Task1 对齐；USCIS 404 source 显式禁用；每文件 SHA scope 避免累计行数误报；原始 header 存字符串 payload 防止 Airbyte 自动重命名/新增列静默丢失；失败下载保持此前 registry/tfvars；board source 按 provider 独立 check/discover，Ashby 使用公开文档保证的 jobUrl；目标本地运行环境 使用未经修改 abctl，当前机器不改变 HOME；CDC 需要两表主键、FULL replica identity、pgoutput、可观测 tombstone。

尚需实际授权/环境：home-ubuntu 项目内状态路径/受控Docker访问/强制≤18GB磁盘与≤16GB合计RAM的隔离 controller、防火墙与安全访问；Airbyte application/workspace；安全远程 state（private key 等 secrets 会进入 state）；可达的既有 HTTPS 原件/派生 archive；可用官方文件链接及年度 layout 验证；Task1 Snowflake 云端对象和 key-pair；非生产 Postgres 副本管理员权限/复制账号/TLS；确认目标本地平台 connector check/discover 成功。**本次未提供这些凭证，也未执行云端部署。**

随后在获授权环境执行初始 sync，保留 Airbyte job id/logs；逐文件运行 `scripts.reconcile_counts` 并保存全为 MATCH/退出0的实际差异 CSV；对两表执行增改删，运行 `scripts.cdc_probe --run` 或等价真实副本 adapter，保存 900s 内的 commit/observation/job/tombstone 证据；观察删除后下游排除 tombstone。到那时才能勾选 Task2 原计划真实验收。

## 当前官方文档依据

- [Airbyte Terraform provider 1.3.0](https://registry.terraform.io/providers/airbytehq/airbyte/1.3.0/docs)；[generic source schema](https://github.com/airbytehq/terraform-provider-airbyte/blob/v1.3.0/docs/resources/source.md)；[connection schema](https://github.com/airbytehq/terraform-provider-airbyte/blob/v1.3.0/docs/resources/connection.md)。使用真实 provider init/validate，未把 inline connector JSON 当作远端 check/discover。
- [Airbyte OSS registry](https://connectors.airbyte.com/files/registries/v0/oss_registry.json) 提供实际 connector version/spec；本地 research 缓存在 `.tools/ingestion/registry.json`。
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)、[Lever Postings API](https://github.com/lever/postings-api)、[Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) 支持本实现的公共 GET、response shape 和分页行为。
- [Postgres source/CDC](https://docs.airbyte.com/integrations/sources/postgres)、[PostgreSQL logical decoding](https://www.postgresql.org/docs/current/logicaldecoding-explanation.html)、[abctl VM deployment](https://docs.airbyte.com/platform/deploying-airbyte/abctl-ec2)、[upstream abctl0.30.4](https://github.com/airbytehq/abctl/releases/tag/v0.30.4)、[API token](https://reference.airbyte.com/reference/createaccesstoken)、[API sync job](https://reference.airbyte.com/reference/createjob)。
- 官方发布页：[DOL](https://www.dol.gov/agencies/eta/foreign-labor/performance)、[USCIS](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/h-1b-employer-data-hub-files)、[BLS OEWS](https://www.bls.gov/oes/tables.htm)。受阻结果如上，未以第三方陈述替代原始下载。

2026-10-24 19:52 UTC，为Task9整财年回填重核来源：DOL发布页当前web访问403；Q1/Q2/Q3精确官方文件名搜索无结果。以已验证FY2024_Q4同目录命名构造的三个候选URL，HEAD均403；再使用现有归档器相同User-Agent做普通GET、限制只读前4096字节，并加入已验证Q4链接作对照，Q1–Q4全部403。此结果只证明本次访问受阻，不能推断三个候选文件不存在或将其登记为已验证发布期；本次未下载大文件，原已归档Q4和SHA证据不变。具体HTTP记录在`.tools/orchestration-research-20261024/dol-fy2024-quarter-{heads,get-prefixes}.json`。当前整FY来源覆盖仍不足；原始回填验收保持OPEN，不能重复Q4或伪造历史职位日来补全年。
