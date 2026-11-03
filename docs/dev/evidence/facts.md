# Task6 事实、合约与增量证据

2026-10-24，本地 `LOCAL_ARCHIVE_REPLAY`，不是 Airbyte/Snowflake 同步或远端验收。
实际库 `.tools/facts/actual.duckdb`；原始来源与 fixture 分开。

## 实际来源及完整核算

| 范围 | 输入 | 事实/处置 |
|---|---:|---:|
| FY2024_Q4/FY2025_Q4 LCA | 239477 | 238891 个最新 case_number + 586 条被替代；无空案件号、无同业务日期歧义 |
| May2025 OEWS RAW/staging | 413527 | 全量保留；1394 个职业码 |
| 全行业/全所有制基准候选 | 237521 | 237514 个唯一职业×AREA_TYPE×AREA×调查年；7 条完全相同估计的重复来源 |
| 行业/所有制范围外 | 176006 | 留在 RAW/staging，不进入 benchmark |
| Greenhouse/Lever/Ashby 原始公开页 | 19/0/67 | 86 条原 UTC 观测日职位；无 CDC demo 混入 |

LCA 是两个 Q4 的实际数据，不代表完整财年。所有 238891 条最新案件保留 canonical
employer_id，但 employer_key 为空且 `PRE_SNAPSHOT_HISTORY_UNKNOWN`；没有把当前
企业属性倒填业务历史。最新事实的职业键可解析 236936 条、地区键 130942 条。
旧完整 LCA 输入的县缺失/歧义 107990 条保持不变；最新案件中对应 107949 条。
旧 SOC 缺陷保留，未通过新增 OEWS 参考替 LCA 修码。

BLS 原件 SHA256：`86ffcbbeb27a96b7aa84d01801de71ee94ee9027452152caabebfdec9d764fde`。
JSONL SHA256：`56ea8a7aa33e1913c332e7d28d9f06742a408e746fb33c9a3fb5f383ced31dfa`。
成员 `oesm25all/all_data_M_2025.xlsx`，sheet `All May 2025 data`，header1，413527 行、32 列。
使用已归档原件，未重新下载。32 列全部显式映射，排除 0 列；年度 map、staging 和
`dbt/source_contracts/bls_2025_headers.json` 保留行业、所有制、所有职业层级、均值、
中位数、分位数、就业量/统计指标/ANNUAL/HOURLY 及原始状态。未知新增列会失败。
May2024 原件仍未取得，实际库的 FY2024 BLS 为空，保留其既有 fixture 合同。

benchmark 层级：total585、major12833、minor94、broad447、detailed223555，共237514。
全部 1394 个职业码及全部职业层级保留，所有事实职业/地区 FK 已解析。
总计 `00-0000` 与 5 个 OEWS 特定码使用 `OEWS_May2025` 命名空间；未改标准 SOC 代码，
未把组合职业工资拆分。地区 AREA 不是县 FIPS，也没有按多个县展开。
7 对全国重复除 O_GROUP 外的原始31字段均一致，canonical 偏好 detailed，保留
`source_first_row_number`、`source_last_row_number`、被选行与计数。具体行对：
77/78、110/111、572/573、611/612、778/779、1044/1045、1162/1163。
新冲突保留诊断并使 dbt build 失败；不会任意选择不同估计。

585 个 BLS area、6 个 OEWS 节点由小型 reference seeds 提供；种子记录发布 vintage、
原件 SHA 和来源行，生成器对 JSONL SHA 强制校验。旧1447个SOC节点、6457个双vintage县
的 surrogate 均无变化；新维度共1453职业、7042地理、81469企业、13149日期。
旧 `int_dimension_mapping` 的所有状态计数与 reviewed Task5 完全一致。

职位由既有 pinned CDK manifest 离线重放原始公开页，保留既有 `job_key`
(provider:board:原ID/官方jobUrl)、payload、原 source URL/SHA。
观察时间是原 `pages.observed_at_utc`：2026-10-24T02:31:09.620313Z / 02:31:13.053914Z，
不是本次运行/记录发出时间。daily 日期为2026-10-24 UTC，尽管当地仍为前一天。
实际 Ashby67 条均 isListed=true；Greenhouse19 条 listing 未声明。所有86条实际 active，
企业/职业/地区映射均明确 unresolved，原文保留。没有用 board 名称猜法人。

## 真正增量与目标故障

三表都是 `materialized=incremental`、`unique_key`、`incremental_strategy=merge`，
没有替换成 delete+insert。源历史保留并重评胜者，晚到旧记录不会覆盖较新案件。
案件以 decision_date/received_date/FY 排序，release/SHA/row 仅稳定 tie-break；
同业务日期重复明确诊断，不把行序说成业务修订时间。

`.tools/facts/checks-v2/evidence.json` 记录27次 native运行：12次成功、15次预期目标拒绝。
两次真实运行各3条 `MERGE INTO`，共6条 SQL，行数与全部业务内容摘要完全不变：

| 实际事实 | 行数 | 排序行摘要 SHA256 |
|---|---:|---|
| fact_lca_case | 238891 | `9efee23605a140f7e396cce45496c7bfbfbe74704e285310c438f6b43cb7dc1f` |
| fact_job_posting_daily | 86 | `043e394558b1a8c211ec64c724d41bbfdf936fceddeded981e74134cf04aab46` |
| fact_wage_benchmark | 237514 | `cd041470b688b566f8afc8e12b06968793c6e40de36d80b63e8129a187a9c708` |

独立 fixture 使用最小实际关系副本并明确作合成变更，不是新的实际市场观测。
案件2→3行、同案件新决策/工资更新、随后晚到旧决策不覆盖；职位2→4行，
覆盖同日更新、次日新增、isListed/tombstone保留语义及合成 postgres CDC删除；
工资4→5行，覆盖原grain更新与新年份插入；随后两次全内容摘要幂等。
fixture 还验证半开时间边界，以及 date-only 跨日内版本边界时不给伪造精确 FK。

- 独立项目复制中把 `annual_wage` 合同改为 VARCHAR；`dbt build --select fact_lca_case`
  exit1，`Compilation Error` 明确指出 enforced contract 失败，
  `annual_wage | DECIMAL(24,2) | VARCHAR | data type mismatch`。
- 三种事实各注入一条重复 grain：unique/facts_grain 真正 fail。
- LCA employer/occupation/geography/date 四类坏键，以及职位 employer_id/version/
  occupation/geography/date 五类坏键，全部 relationships fail。职位另有明确非空有效
  synthetic FK 的通过运行；实际职位全 NULL FK 的通过没有被当成实际解析证据。
- 新增 BLS payload 列使 `facts_bls_headers` fail1；一对全国重复改为不同估计使
  `facts_bls_conflicts` fail2。原项目/真实库未留故障。

以上预期拒绝全部检查 run_results 的目标 node/status/message，不接受缺node、凭证或
连接错误作为故障通过。早期宽选择触发未复制的跨域旧测试缺node，以及一次并发文件锁
错误，仅是运行调试错误，不计入上述27次有效证据。

## 命令、内容与保护

复现命令见 `dbt/README.md`。本次完整真实构建 `.tools/facts/complete-build-v2.log`：
`PASS=48 WARN=0 ERROR=0 SKIP=0 TOTAL=48`。相关旧回归：
`pytest scripts/tests/test_dimensions.py scripts/tests/test_board_manifest.py scripts/tests/test_staging_local.py -q`
为32 passed；Task6 pytest4 passed；Ruff及diff whitespace检查通过。
Snowflake adapter1.12.4的 `dbt parse --no-partial-parse` exit0，仅静态parse，未连接远端。

`python -m scripts.fact_audit` 逐条比较12项OEWS均值/median/分位数与对应原RAW字段，
全部 mismatch=0；LCA胜者工资/状态/日期、职位原payload/SHA/观测日期 mismatch=0，
无事实存在更新的源decision。详见 `.tools/facts/content-audit.json`。

`.tools/facts/load-evidence.json` 中40个保护文件的 SHA256/size/mtime_ns 前后完全一致，
审计结束再次验证相同；包含新BLS ZIP/JSONL/provenance、所有RAW原件、reviewed staging、
Task5库和ER输入/模型/registry/xref等。没有修改旧RAW/staging/ER/snapshot语义。

2026-10-24T17:15:31Z资源口径：`du -sB1 .` 实际分配10748637184字节，
`du -sb .` unique-inode apparent10475265173字节；逐路径size累加13821393354字节
会重复计算硬链接，属于保守上界，均低于18GB。facts目录分配1800568832字节，
实际事实DB1722822656字节。未继续生成大副本；DuckDB4GB/1thread/2GBspill，
没有云资源、账号、GPU、push、发布或常驻服务。

Snowflake远端、Task3原始质量、人工ER的0/0与P/R/Free全量等仍OPEN；
没有把本地技术通过说成这些质量或外部验收通过。不同年份OEWS不默认严格可比，
官方median不会以均值或多个median平均替代，抑制/顶码原值不补零。

## Fix1：独立维度入口的测试选择边界

原 Task5 默认 eager 命令在新增事实后误选13个事实 relationships 与facts_accounting，
在没有事实表/FACTS_INPUT的库中产生14个缺表错误；最初32项Python回归未覆盖这个native入口。
公开维度命令及 `scripts.dimension_checks` 现在显式采用 cautious，完整构建直接选择
scd2_no_overlap、dimension_reference_integrity、dimension_mapping_accounting；具体迁移命令见dbt/README。
没有改全项目默认策略或禁用/修改任何质量测试。

`.tools/facts/fix-r1/inspection.json`核实：独立旧Task5副本build30节点全部通过，
三个核心测试实际执行且通过，无FACTS_INPUT/事实表；SCD2 native16轮（9成功/7预期数据拒绝），
版本81469→81470且幂等；现有事实fixture默认选择执行29项测试，含全部13个FK、grain、
accounting/header/conflict，全部通过。新native selection pytest先1失败/2通过，修复后3通过，
同时锁定旧staging默认六类质量门禁的选择。40个保护源及parent提供的旧Task5副本SHA/size/mtime不变。
