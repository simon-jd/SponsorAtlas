# Task10 成本与性能证据

2026-10-24。**本地实现已验证；真实 Snowflake 20 查询、聚簇/规格/缓存费用对比仍 OPEN。** 未提供试用凭证，本任务没有连接远端、创建 Azure 资源或宣称 Snowflake 加速/节费。Task9 的 fullFY/真实远端门禁不因此关闭。

## 报告口径

`scripts/query_profile_report.py remote` 从 `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` 左关联 `QUERY_ATTRIBUTION_HISTORY`，限定 `SPONSORATLAS_XS`、精确 Task10 tag 和最长 24 小时 UTC 窗口，按 `credits_attributed_compute DESC NULLS LAST, query_id` 返回最重 **20** 条。输出 elapsed/execution 毫秒、扫描/总分区、扫描字节、仓库磁盘缓存比例、结果读取字节及 credits，不输出账户 query_text、用户名或密钥。私有恢复 journal 另保留本执行器生成的固定维护 SQL intent、session 和 provider ID。

归属计算用量不含 idle；QAS 单列；cloud-services 数值是未每日调整的独立量，不能当总计算费用。归属最多延迟 8 小时，约 100ms 以下短查询可能没有记录。缺记录/NULL 是 `MISSING_DELAY_OR_SHORT_QUERY`，真实零保持零；所有度量拒绝负数、布尔、非有限数；并列按 ID 确定排序。参见 [QUERY_ATTRIBUTION_HISTORY](https://docs.snowflake.com/en/sql-reference/account-usage/query_attribution_history) 和 [QUERY_HISTORY](https://docs.snowflake.com/en/sql-reference/account-usage/query_history)。

仓库账单使用另行显示的 `WAREHOUSE_METERING_HISTORY` 整小时桶，包含同仓库其他 tag 和 idle，不能分摊成单次看板费用。每次恢复 warehouse 有最低 60 秒计费；resize 也可能增加用量。[Warehouse considerations](https://docs.snowflake.com/en/user-guide/warehouses-considerations)。Azure 美元台账、试用余额、查询归属和仓库总用量互不替代。

## 可复现的本地结果

```bash
.venv/bin/python -m pytest scripts/tests/test_query_profile_report.py::test_report_ranks_by_credits -q
# RED（实现前）：ModuleNotFoundError: No module named 'scripts.query_profile_report'
# 1 failed in 0.02s
.venv/bin/python -m scripts.query_profile_report fixture scripts/tests/fixtures/task10-query-history.json .tools/task10/fixture-report.json
.venv/bin/python -m pytest scripts/tests/test_query_profile_report.py scripts/tests/test_query_profile_native.py scripts/tests/test_facts.py -q --basetemp=.tools/task10/final-native-v2
# 24 passed in 21.15s（最后的小范围修正另有profile focused复跑）
```

fixture 明确标注 `SYNTHETIC_QUERY_HISTORY`，不会连接 Snowflake；排序 high→low→zero→missing，与 cloud-services 排序相反。原生 dbt 测试只从实际库只读投影两条候选与相关维度，然后在独立小库运行：初建成功→删掉新列后的增量拒绝→显式 full-refresh 成功→真正 MERGE 成功→错类型合约拒绝→恢复构建/关系测试成功。保留事件粒度、缺州、历史 UNKNOWN；没有删除坏行求绿。独立测试不能替代 fullFY。

在 `semantic` 目录运行：

```bash
../.tools/node-runtime/node-v24.22.1-linux-x64/bin/node --max-old-space-size=512 --test --test-force-exit --test-concurrency=1 tests/snowflake.test.ts tests/snowflake-lifecycle.test.ts tests/cost-refresh.test.cjs
```

私有 `semantic/scripts/cost-refresh.cjs` 真正运行 Cube 1.8.0/Node24 REST 七指标，复用 canonical 模型和 `semantic/projections.json`；两个新工作目录使用不同 revision，分别建两张物理 rollup，所有驱动关闭后以只读实例重新打开实物验证行数与 REST。两阶段结果 hash 相同且物理表名不同。原生 fixture 是 DuckDB，不产生 Snowflake ID/credits。`--test-force-exit` 结束固定 Cube 测试运行器残余句柄；每次刷新本身显式 shutdown/release/闭库验证，CLI 私有 worker 完成后退出。生产 `source.cjs` 的 CUBE_SVC/ANALYST/MARTS/XS/60 与只读守卫未改，相关 20 项回归通过。

本轮本地完整刷新耗时为 0.777s / 0.648s（REST返回分别0.716s / 0.596s），结果 hash 均 `17ace06076b9088d024796816bbb9d158afcf007994e47cb72642acb56b3c7ed`；查询 ID 为空，credits 未观测，不推断费用或加速。该进程 maxRSS 829,416 KiB，不是远端性能数据。

## 事件州、聚簇与旧 schema 迁移

`fact_lca_case` 显式输出获胜源事件 `w.worksite_state`，上游 staging 为 `upper(trim(worksite_state_raw))`；不从雇主地址、县键推断。YAML 合约类型 `varchar`，缺值/异常源州仍保留；增量 MERGE、`on_schema_change='fail'` 与强制合约不放松。

默认不自动启用收费维护。Snowflake 专用配置 `--vars '{task10_enable_clustering: true}'` 才启用 `(fiscal_year, worksite_state)` 聚簇键；DuckDB 不启用。已有 schema 必须先在隔离目标验证明确的重建：

```bash
# 只对显式选定、已有完整源历史的可重建目标；不得指向受保护 actual.duckdb。
.venv/bin/dbt build --project-dir dbt --profiles-dir dbt --select fact_lca_case --full-refresh
```

维护窗口暂停生产刷新/写入，保留旧表/备份与原 grain/FK/accounting/rejects 基线；新目标核验 source SHA/row、总数、唯一 case、关系、原列内容与事件州后才切换。失败回到旧目标，不手工添加全 NULL 新列绕过 backfill；迁移不自动覆盖保护库。本地测试演练此拒绝/重建路径。完整实际库迁移与 fullFY 仍 OPEN。

## 私有真实试用实验接口（2026-11-01 已执行一次：P9A，状态 MEASURED_ATTRIBUTION_PENDING，见 warehouse-foundation.md）

观测身份与实验身份独立，均 JWT key-pair，RSA ≥2048 且密钥文件 0600，不加载 `.env`，不接受密码、无限试用或编排 synthetic 模式。需要操作者明确设置：

| 配置 | 要求 |
|---|---|
| `SPONSORATLAS_COST_MODE` / `SPONSORATLAS_COST_OPT_IN` | `production` / `YES` |
| `SNOWFLAKE_TRIAL_STARTED_AT` / `SNOWFLAKE_TRIAL_EXPIRES_AT` | 显式 `YYYY-MM-DDTHH:MM:SSZ`，活跃且 ≤30 天；工作外预留420秒 |
| `SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_COST_PRIVATE_KEY_FILE` | 用户提供真实试用账号与本机 owner-only RSA key |
| `SNOWFLAKE_COST_PRIVATE_KEY_PASSPHRASE` | 仅加密私钥需要，不写证据 |
| `SNOWFLAKE_COST_USER` / `SNOWFLAKE_COST_ROLE` | 报告为 `COST_OBSERVER_SVC` / `COST_OBSERVER`；实验为 `COST_EXPERIMENT_SVC` / `COST_EXPERIMENTER` |
| `SPONSORATLAS_TRIAL_REMAINING_CREDITS` / `SPONSORATLAS_COST_MAX_CREDITS` | 操作者核验的有限正数余额和不超余额的本次额度 |
| `SPONSORATLAS_COST_EXPERIMENT_OPT_IN` / `SPONSORATLAS_WAREHOUSE_EXCLUSIVE` | 可变实验额外要求 `YES` / `YES`，停用其他仓库作业 |
| `SPONSORATLAS_CLUSTERING_MAX_CREDITS` | 独立显式维护 allowance，不是硬计费上限 |

最小权限由管理员在用户批准的试用内预配置，本代码不授予：观测 role 使用 `SNOWFLAKE.USAGE_VIEWER` 数据库角色读取使用量视图和项目仓库 USAGE；实验 role 对 `ANALYTICS` USAGE/CREATE SCHEMA、对 MARTS USAGE 与六张明列公共表 SELECT、项目 warehouse USAGE/MODIFY/OPERATE/MONITOR、指定 resource monitor MONITOR；只拥有自己创建的实验 schema/克隆。不要授予 ACCOUNTADMIN、MANAGE GRANTS、全账户仓库管理，也不要扩大 CUBE_SVC 权限。资源 monitor 必须预先绑定项目仓库、级别 WAREHOUSE、正 credit_quota ≤ allowance、尚未耗尽且 immediate suspend 阈值 ≤100%；资源监视器可能延迟/超量，不是严格费用 cap。

```bash
.venv/bin/python -m scripts.query_profile_report remote --start 2026-10-24T00:00:00Z --end 2026-10-25T00:00:00Z --tag SponsorAtlas_Task10 --output .tools/task10/remote-report.json
.venv/bin/python -m scripts.query_profile_report experiment --run-id UNIQUE_RUN --fiscal-year 2025 --state CA --output .tools/task10/experiment.json
# 至少等待归属视图成熟后，以观测身份运行；取实际记录的开始/结束窗口：
.venv/bin/python -m scripts.query_profile_report reconcile .tools/task10/experiment.json --start 2026-10-24T00:00:00Z --end 2026-10-25T00:00:00Z --output .tools/task10/attributed.json
# 仅处理固定 durable journal 中的那一次未决实验：
.venv/bin/python -m scripts.query_profile_report recover --output .tools/task10/recovery.json
```

上例 UTC 是调用格式示例，真实调用须使用实际试用和测量窗口。输出采用 exclusive create，不覆盖既有证据。

实验只创建带随机 ownership token 的 `ANALYTICS.TASK10_<run>` schema，逐表零拷贝 clone 三公共事实及 dim_occupation/geography/date，禁止整 schema clone。已有同名 namespace 拒绝；清理前核验 exact name/comment ownership。六表 count/hash 跨阶段一致，源库不 ALTER。执行 baseline XS/cache-off→配置聚簇/cache-off→Small/cache-off→XS/cache-prime→XS/cache-repeat；每阶段包含固定源 SQL 补充探针和实际私有 Cube 物理刷新，分别记录 elapsed、query IDs、cache flag、结果 hash。两种范围不混称端到端。

每次 Cube 使用全新 work/revision，验证 fresh 物理表，防止把同一 REST 缓存当刷新；只有 Snowflake 结果复用可以跨新 Cube worker 命中。`USE_CACHED_RESULT` 与 warehouse 数据缓存不同；`bytes_read_from_result` 和磁盘缓存比例作为原始信号展示，命中本身保持 UNKNOWN，不能依据快慢或缺失 credits 判定。[Persisted results](https://docs.snowflake.com/en/user-guide/querying-persisted-results)。

聚簇配置后保存 `SYSTEM$CLUSTERING_INFORMATION`；不假定异步维护已完成或必有改善。2026-09-01后新聚簇表可能为 Optima，记录其实际信息以及延迟 `AUTOMATIC_CLUSTERING_HISTORY.VERSION/credits_used`。恢复 Optima 可产生 catch-up 维护费用；resource monitor 不覆盖 serverless。应先核验试用可用功能和维护 allowance，不能把时间盒当硬费用上限。[Automatic Clustering](https://docs.snowflake.com/en/user-guide/tables-auto-reclustering)。

控制连接工作上限300秒，独立收尾预留120秒，整体截止取开始后420秒与试用到期前420秒中的较早值；较窄试用窗口优先保留收尾时间。连接、执行、fetch、状态轮询、取消确认和关闭连接共用这些绝对截止；每次实际 SQL 派发按当时剩余秒数下调 timeout（≤30秒），POSIX 主线程 wall timer 中断阻塞/重试。收尾最多16次 SQL/状态请求，晚返回保持 OPEN，截止后停止新请求。此 CLI 依赖 Linux/POSIX 主线程，不支持在后台线程绕过 deadline 运行。Cube 每阶段≤60秒/≤60条派发和原生命周期守卫不变。

每条 CREATE/ALTER/DROP 在派发前持久记录 exact intent 与 originating session，收到 provider query ID 后在 fetch 前同步；同步成功应答记录终态，模糊错误保留 UNKNOWN。恢复先用 SDK `get_query_status` 确认已知 ID，必要时发 `SYSTEM$CANCEL_QUERY`，再确认 SUCCESS/ABORTED/FAILED 终态；取消应答、warehouse ABORT、瞬时 SHOW 缺失都不能替代终态。参见 [Connector query status](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api#get_query_status) 与 [SYSTEM$CANCEL_QUERY](https://docs.snowflake.com/en/sql-reference/functions/system_cancel_query)。不需要扩大服务身份权限来猜全账户未知操作。

只有全部 mutation 已证明终止，才在 exclusive maintenance 范围执行 warehouse ABORT、核验/停止自有聚簇、删除自有 schema、恢复 XS/60、SUSPEND 与 result cache TRUE，再核验新清理 mutation 终态和 SHOW 状态。未知/丢失 query ID、旧版 journal 无 mutation ledger 或收尾 deadline 耗尽时继续 OPEN；不能因为对象暂不存在或旧 VERIFIED 标记而解除 barrier。需取得真实 operation/session 证据后再处理，代码不猜 ID、不重放未知 DDL。每个 worker 在连接前独立 fsync 完整目录链（包括其他 worker 已创建的祖先直到根），journal 文件/rename/parent 同步，flock 保持单主机独占。`recover` 必须匹配 exact journal token，禁止按模式批量删除。

`reconcile` 验证 namespace/run/phase 对应的 source exact tag 与其 `_cube` exact tag，再按真实 query IDs 补取归属；不用 LIKE prefix，也不按时间比例估计。任何 query 缺失则总 credits 为 NULL，同时展示已归属单项。独立显示仓库整小时桶和聚簇维护历史，不声称这是完整账单。真实缓存命中、有效聚簇改善、完整 query 费用及最终恢复都须实际账号证据验收。

第1轮审查修复仅复跑 `scripts/tests/test_query_profile_report.py` 与 `scripts/tests/test_query_profile_recovery.py`：39项通过（0.11s），ruff及diff-check通过。新回归起始8项全RED→8项GREEN，扩展覆盖 exact tag/其他run/反斜杠、工作/收尾/试用截止及阻塞中断、未知DDL/取消ACK/旧journal、首次并发目录与祖先屏障失败。未修改或重跑既有 Cube/dbt/大库套件；上述原生证据仍为上一轮点位。

## 保护与资源

受保护 `.tools/facts/actual.duckdb` 仍为 1,722,822,656B，SHA-256 `c5c5b2ed0544146f2735682c5ab388930f5e7221c01a910df96561d1a4785e7a`。仅 READ_ONLY attach，未复制整库；Task9 evidence/work 未写入。没有依赖安装、云/GPU/常驻服务、HOME 变更或 push。Task10 的原生 scratch 和证据只在 `.tools/task10`，与控制者最新资源核算相加仍须低于18e9字节，服务合计≤16GB；不沿用不含 Docker 层/PG 的目录小计充当全项目用量。

本地原生及资源原件 保存 dbt 六步骤、Cube 物理结果与控制者 **21:32:22 UTC** 点位：保守总计 **13,457,822,266B**，已包含该点位的 Task10 文件；不要再加一次全部 Task10 目录。口径为 `max(路径11,607,161,654,分配11,671,252,992)+停机PG111,742,976+镜像双计1,644,752,570+RW73,728+logs30,000,000`，named volumes空。唯一walk权限缺口PG按既有实测最大值另计；三个治理容器自19:37停机且未重启。该点位后仅新增KB级文档/JSON/提交，没有新安装/镜像/服务；不能将点位冒充未来实时数。

2026-11-01 已用用户提供的试用账号和身份执行一次实验（P9A，见 warehouse-foundation.md），8 h 后对账状态仍为 `MEASURED_ATTRIBUTION_PENDING`，各阶段 credits 大多未归因。真实性能与成本验收仍为 OPEN。
