# Task4 雇主实体解析：local-ready / original-incomplete

## 2026-10-29：部署阈值 0.875（7/8）

**用户在 2026-10-29 把部署阈值定为 0.875。这个值是在已经看过 holdout 结果之后选的**，所以下面的 holdout 数字不是无偏的留出估计。阈值只在 `er/settings.py` 的 `THRESHOLD` 里设；`er/tests/test_threshold_constant.py` 扫描 er/scripts/orchestration/dbt/semantic/governance 的代码和说明，任何别的阈值字面量都会让测试失败。

实际情况：2026-10-28 Cursor 只改了常量，从没按 0.85 重跑过；当时的规范产物 `prediction.json` 写的是 `threshold: 0.9`（2026-10-27 构建）。

重跑（`backfill run --from-step er_export`，规范路径不变）：er_export → er_train → er_predict → dimension_inputs → dbt_dimensions → fact_inputs → dbt_facts 全部 exit 0，共 99.7 s（32 核，负载 3.1–3.9，其他项目在跑）。必须从 er_export 开始：e8267fe 之后 `load_dimension_inputs` 只读 `released` 的 USCIS 行，旧 ER 输入还含空地址行，直接接下游会报 lineage mismatch。重训用同一 seed42、同一设置、同一空 `er/labels/pairs.csv`，没有调别的。之后 `make semantic-actual`（Cube 9/9）和 `make viz-worksite-export`。原始输出：[task04-er-threshold-0875/](../../evidence/task04-er-threshold-0875/)。

冻结 subagent 参照标签（`agent-annotation-20261025/revision-2`，不是人工标签），同一份 scores.parquet，按最终簇计：

| 阈值 | holdout TP / FP / FN / TN | P | R | train TP / FP / FN / TN | 接受边 | 簇 | 雇主 |
|---|---|---:|---:|---|---:|---:|---:|
| 0.85 | 42 / 0 / 4 / 47 | 1.0 | 0.9130434782608695 | 115 / 0 / 11 / 245 | 107,780 | 98,197 | 98,188 |
| **0.875** | **42 / 0 / 4 / 47** | **1.0** | **0.9130434782608695** | 115 / 0 / 11 / 245 | 107,780 | 98,197 | 98,188 |
| 0.9 | 42 / 0 / 4 / 47 | 1.0 | 0.9130434782608695 | 115 / 0 / 11 / 245 | 107,780 | 98,197 | 98,188 |

- 三个阈值之间：接受边、簇、雇主的变化都是 **0**。[0.85, 0.9) 里只有 211 个候选，全被上下文规则（207 INSUFFICIENT_CONTEXT）或强 ID 冲突（4）否决，所以阈值在这一段不起作用（[band-and-input-diff.json](../../evidence/task04-er-threshold-0875/band-and-input-diff.json)）。
- holdout 12 对弃权；难例 32 / 0 / 4 / 3（R 0.889）。4 个漏判：3 个分数低于 0.85，1 个上下文不足。
- 600 对里 559 对能映射到当前全年输入；41 对（train 38、holdout 3）有一端是 40 条现在 `released=false` 的 USCIS 空地址记录，不计入。
- 和 2026-10-27 规范产物（阈值 0.9，输入含空地址行，164,059 条记录）比：记录 −2,463（全是 USCIS），接受边 114,646 → 107,780（−6,866，没有新增边），雇主 98,343 → 98,188（−155）。两边都有的 161,596 条记录里，没有一个簇的成员变了；但 1,616 条记录（599 个旧 employer_id）换了 ID，因为 backfill 用 `--initialize-registry`，原来的锚点是被丢掉的 USCIS 行。所以 Cube `lca.parquet` 有 55,202 行只在 `employer` 列不同，地图投影 517,991 行中 33,044 行不同（[export-diff.json](../../evidence/task04-er-threshold-0875/export-diff.json)、[employer-id-diff.json](../../evidence/task04-er-threshold-0875/employer-id-diff.json)）。事实行数不变：713,467 LCA。旧产物在 3 个阈值下 holdout 都是 44 / 0 / 4 / 47（[before-canonical-20261027.json](../../evidence/task04-er-threshold-0875/before-canonical-20261027.json)）。
- 注意：规范模型训练时 `excluded_holdout_records = 0`（`pairs.csv` 为空），u/EM 见过 holdout 端点（无监督，不用标签）；2026-10-27 的召回实验在 Q4 输入上排除了这些端点，得到 43 / 0 / 5 / 47。两组数字不可直接比较。R ≥ 0.90 只是对 subagent 参照成立，不是人工验收。

## 2026-10-28：阈值改为 0.85

用户把匹配阈值从 0.9 定为 0.85。P ≥ 0.97、R ≥ 0.90 的目标没有改。没有重新训练：`task04-er/final-report.json` 里 0.8 和 0.9 的簇计数完全相同（holdout TP 43 / FP 0 / FN 5 / TN 47，P = 1，R = 0.8958333333333334），0.85 落在这段平坦区间里，所以这次改动不改变那次留出集的 P/R，召回仍然差 0.90。人工标签（D5）暂停，agent 标签仍不是最终验收。

## 2026-10-27：召回改进（当前结果，subagent 标签）

命令：`.venv/bin/python -m er.recall_experiment --input .tools/er/real-input-v2 --labels .tools/er/agent-annotation-20261025/revision-2/agent-pairs.csv --output <新目录> [--evaluate-holdout]`。
原始输出：task04-er/（`train-only-report.json` 设计阶段只看 train；`final-report.json` 设计定稿后唯一一次 holdout 评估；`training.json`；`inputs.sha256`）。
标签是 600 对 subagent 标签（不是人工标签），split 沿用冻结的 train/holdout；u/EM 训练排除全部 209 个 holdout 端点。种子 42，阈值固定 0.9，没有调。

改动（只根据 train 上的漏判原因设计）：
1. 新 blocking 规则 `name_norm + tax_last4`（不要求同州）：train 上 35 个正例漏判原因是没有候选，大多是 USCIS 一侧没有州。
2. 规范名完全相同、税号后四位相同时，可以替代"同州 + 同城市/邮编"的上下文要求（另一个办公地址，或 USCIS 行没有地址）。仍需 p ≥ 0.9。
3. 同一个美国 EIN、同一个邮编时，忽略 LCA `EMPLOYER_COUNTRY` 的冲突（实际值如 GEORGIA、NAMIBIA、INDIA），合并后的簇只保留 US。

| 阈值 0.9，按最终簇计 | TP | FP | FN | TN | P | R | 目标 |
|---|---:|---:|---:|---:|---:|---:|---|
| 原模型 holdout（2026-10-25） | 28 | 0 | 20 | 47 | 1.000 | 0.583 | 未达 |
| 原模型 train | 69 | 0 | 89 | 250 | 1.000 | 0.437 | 未达 |
| 新模型 train | 147 | 0 | 11 | 250 | 1.000 | 0.930 | 达到 |
| **新模型 holdout** | **43** | **0** | **5** | **47** | **1.000** | **0.896** | **R 未达（差 1 对）** |
| 新模型 holdout 难例 | 31 | 0 | 5 | 3 | 1.000 | 0.861 | — |

holdout 有 13 对弃权，不计入。holdout 的 5 个漏判：3 个低于阈值（名称缩写/DBA 变体，例如 univ↔university、dist↔district、带 "f k a" 尾巴），2 个上下文不足。

阈值曲线（P / R）：

| 阈值 | train | holdout |
|---|---|---|
| 0.5、0.7 | 0.994 / 0.994 | 0.979 / 0.958 |
| 0.8–0.99 | 1.000 / 0.930 | 1.000 / 0.896 |

0.7 在两个 split 上都能同时满足 P ≥ 0.97、R ≥ 0.90，但这是看到 holdout 之后才知道的，阈值仍保持 0.9；要改需要用户决定，最好再配一批新的 holdout 标签。
全量决策：候选 354177 对，接受 72835 条边（原 52389）。一次运行约 20 秒、产物 77 MB。
`er/tests/test_evaluate.py` 的真实验收仍要求 500 对人工标签，现在仍然 skip/失败；Databricks 全量也没跑。

2026-10-24 UTC。本地真实 staging → Splink5.0.0/DuckDB2.0.0 随机u/EM/predict 已完成；**人工标签0对、人工难例0对，P/R未验收；Databricks Free未运行**。不把合成回归或Spark协议测试算原始验收，也不覆盖 Task3 尚未通过的真实质量/BLS/Snowflake门禁。

## 实现与输出契约

`er.export` 只选 LCA/USCIS，核对版本化官方原件SHA/发布版本，排除 BLS/jobs fixture。按 `(source_system,source_employer_key)` 去重属性，所有原始行另存 occurrences（原件SHA、release、row_number、URL）。缺键时按原件行分配来源记录ID并显式未解析，不使用 case_number、职位ID或裸source_name联接。

`normalize` 转小写、去标点、只移除末尾 LLC/INC/CORP/CO/LTD/LLP/PLLC、合并空白；`Amazon.com Services LLC → amazoncom services`。US EIN发行命名空间来自已知美国来源字段，居住国家另存；完整9位格式、当前有效IRS前缀、非掩码、非已知占位值才是强标识，不声称做过IRS所有权验证。末四位只作弱证据，已知无效完整ID不退化成末四位匹配。按[IRS当前有效前缀](https://www.irs.gov/businesses/small-businesses-self-employed/valid-eins)及[IRM21.7.13](https://www.irs.gov/irm/part21/irm_21-007-013r)（2026-10-24核对），历史/暂停发行前缀需复核；实物3条96前缀保留原值并标EIN_PREFIX_REQUIRES_REVIEW，不用作确定性强ID，不断言历史号码不存在。

名称首token+州，以及完整强ID为blocking。固定seed42、prior0.0001、u最多2m随机对、两轮EM；不借人工holdout调参。Splink概率原样保存。阈值0.9以上的概率边还须满足州+城市或ZIP上下文；同名异企、不同完整强ID、已知国家冲突、末四位冲突可否决。强ID相等可作为确定性边，仍受国家/输入与整簇强ID冲突约束；不把规则分数伪装成Splink概率。每条边保存实际概率、method、accepted、reason；整簇维护强ID集合，阻止中间弱记录将不同完整强ID传递合并。

修复后当前本地 `.tools/er/fix-r1-next/analytics.duckdb` 提供 **ANALYTICS.intermediate.int_employer_xref**，含必需四列及source key、州/国家/城市/ZIP、unique_id、issues、resolution_status、match_method；另有 `intermediate.employer_source_occurrences`。xref的概率是该记录最高已接纳直接边的真实Splink概率，不是整簇可信度；singleton/未解析为NULL，确定性强ID边可能低于0.9且有独立method。非空来源键唯一，下游按来源系统+key联接，不能按source_name。

canonical ID由不可覆盖的`registry.json`继承：首次必须显式`--initialize-registry`，只在首次以组件最小来源UID选锚；正常后续必须`--previous-registry`并验证其内容哈希与文件SHA，新加入更小UID不改旧employer_id。注册表保留来源UID→ID及active状态、当前两份输入SHA、父registry SHA，预测manifest绑定本次与父artifact。多旧ID合并、旧ID拆分、旧已解析来源变为无效均报IDENTITY_REVIEW_REQUIRED（CLI exit3），不自动迁移历史自然键。历史来源撤回会保留inactive映射；若存在完全缺席的历史身份且出现无旧锚的新组件，不能区分新企业与替换来源，明确拒绝猜测。历史上限250k来源/128MiB artifact，超限拒绝；尚无人工merge/split迁移工具，不用初始化绕过历史。来源缺席不能视为企业注销或自动关闭SCD2。singleton/缺州ID仍受人工质量门禁。

## 实物执行与可重放命令

所有模型/数据库/缓存在项目`.tools`；每条命令输出新目录，拒绝覆盖。依赖由根extra/lock固定，无需重新下载四原件。

```bash
.venv/bin/python -m er.export --staging .tools/staging/actual-files.duckdb --output .tools/er/real-input-v2
.venv/bin/python -m er.train --input .tools/er/real-input-v2 --output .tools/er/fix-r1-model --labels er/labels/pairs.csv
.venv/bin/python -m er.predict --input .tools/er/real-input-v2 --model .tools/er/fix-r1-model --output .tools/er/fix-r1-initial --initialize-registry
.venv/bin/python -m er.predict --input .tools/er/real-input-v2 --model .tools/er/fix-r1-model --output .tools/er/fix-r1-next --previous-registry .tools/er/fix-r1-initial/registry.json
.venv/bin/python -m er.label_workflow --input .tools/er/real-input-v2 --predictions .tools/er/real-prediction-v2 --output .tools/er/review-candidates-v2.csv
.venv/bin/python -m er.evaluate --input .tools/er/real-input-v2 --model .tools/er/fix-r1-model --predictions .tools/er/fix-r1-next --labels er/labels/pairs.csv --output .tools/er/fix-r1-evaluation.json
```

实际四原件332792行完整谱系保留：LCA FY2024_Q4=120897、FY2025_Q4=118580，USCIS FY2022=59983、FY2023=33332。去重117236来源记录（LCA42773/USCIS74463）；缺名8、缺州2515、缺键0，弱/无效TaxID74220（含前缀待复核3条）。117228条参与；未静默丢弃缺名记录。

EM实际blocking训练338985对（首token+州）、10986对（强ID），候选上界349971；实际predict341701对。日志输出`Your model is fully trained. All comparisons have at least one estimate for their m and u values`，manifest的untrained_parameters为空；第二轮某弱ID负级别未观测，但第一轮已提供估计。首次200k样本有未估计近似名称u，扩大到2m后消除；未使用holdout调参。

| 输出 | 实际数量 |
|---|---:|
| LINKED来源记录 | 60003 |
| SINGLETON来源记录 | 54710 |
| REVIEW_MISSING_STATE | 2515 |
| UNRESOLVED缺名 | 8 |
| 暂定组件ID | 81469 |
| 接纳边 | 52389 |
| 完整强ID冲突否决 | 110652 |
| 已知国家冲突否决 | 28 |
| Tax末四位冲突否决 | 164769 |
| 上下文不足否决 | 13342 |
| 低于阈值否决 | 521 |

以上是未标注模型的操作统计，**不是TP/FP/FN或质量指标**。SQL核验：xref117236行、occurrences332792行、source-key fanout=0、含多个完整强ID的输出组件=0。当前代码重建xref与落盘xref完全一致。重复u/EM的model.json SHA完全相同。

DuckDB设置实际读取为4GB/1线程/2GBspill（格式化显示3.7GiB/1/1.8GiB）；临时目录显式项目`.tools`。候选上限5m、来源记录250k，超限失败，无无限全笛卡尔积。`/usr/bin/time -v`：训练wall6.01s、峰值RSS402912KiB；预测wall5.66s、峰值RSS1258344KiB；各exit0。项目约6.5GiB，ER产物约780MiB，低于18GB；无新增常驻服务。

初版实物证据保留于`.tools/er/real-*-v2`；本轮当前产物为`fix-r1-model`、`fix-r1-next`及其父`fix-r1-initial/registry.json`，详见末节。记录SHA（本轮仍相同）：

- records.parquet `c34ff3dddb56a6872063a7df9416e975aa6dd31ea8c9db565c6ac41addf1fa8a`
- occurrences.parquet `251345f31ab567979ccdad1d58fc59b208c64d0b6525061e2eec7e1ccb669c10`
- model.json `5e606bce4c9d9deb3292d6f914125047227bc766b0d3baee00b459ae56dac2ed`
- scores.parquet `9c34d67e5c84ad3aa181f1e4865ee7def03cb8648456a1b7cea494cc28ba26ae`
- xref.parquet `f3c238b539b9b269e5c67654e4c4d9c29c1626c492a583cedbeeaaa73be03e9c`

## 2026-10-29：人工验收（PASS）

- 标签：用户 Junrui 盲标冻结 revision-2 的 600 对（表里没有 agent 标签和置信度）。`er.label_import` 导入 559 对到 `er/labels/pairs.csv`；41 对一端是已排除的 USCIS 空地址记录，跳过。holdout 105 对（55 正、50 负、42 难例），train 454 对，难例合计 243。
- evidence 列：标注表把它标成可选，用户没填。导入时用 `--evidence-from-source-rows` 写成标注表给审阅者看的原件行，并注明“审阅者未另附外部证据”。
- 重训 `er.train --labels er/labels/pairs.csv`：u/EM 排除 203 个 holdout 端点，`acceptance_eligible=true`。全年输入上按 INHERIT 预测（父 registry 是归档的规范 registry）：0.875 下接受边 107,780、雇主 98,188，和部署版逐条一致（161,596 条记录 0 处不同）。
- `er.evaluate`（0.875，人工标签）：holdout TP 50 / FP 0 / FN 5 / TN 50，P = 1.0，R = 0.9090909090909091，达到 P ≥ 0.97、R ≥ 0.90，exit 0。0.8、0.85、0.9 结果相同；0.5、0.7 和 0.95 以上在 INHERIT 下要先审身份迁移（MERGE_REQUIRED / SPLIT_REQUIRED），没有数。难例 42 对：TP 30 / FP 0 / FN 3 / TN 9。18 对 holdout 没有直接候选分数，按簇传递计。
- 5 个 FN 都是漏合并：DBA、简称或截断写法 3 对，后缀/缩写差异 1 对，地址一致但税号一侧缺失 1 对。
- 显式验收测试 `test_evaluate_meets_thresholds`：1 passed。
- 和 2026-10-25 的 subagent 参照比：两边都有结论的 464 对完全一致（holdout 93/93）；subagent 弃权的 95 对由人工给了结论。
- 局限：阈值 0.875 是看过 subagent holdout 结果之后定的；只有一名审阅者，没有第二人复核；`reviewed_at` 是导入时间。部署的仍是无标签模型（簇相同），下一次 backfill 会用这些标签训练。
- 证据：`docs/evidence/task04-human-acceptance-20261029/`（report.json、acceptance-test.json、training.json、prediction.json、comparisons.json、日志）。

## 人工评估门禁与难例

`er/labels/pairs.csv` 仅表头；`.tools/er/review-candidates-v2.csv` 是600对**未标注**实物工作项，其中108对固定holdout，label/hard_case/reviewer等均空，不能计成人工标签。候选覆盖接纳/冲突/低分/同名跨州/随机源对；候选图连通分量以seed42哈希整体切分，避免同一记录跨split。详见 `er/labels/README.md`。

真实evaluate的实际stdout与exit：

```text
{"status": "BLOCKED", "error": "requires at least 500 human-labeled employer pairs (100 hard cases)"}
exit=2
```

500人工对、100人工难例、审阅者/含时区时间/逐对来源与理由、冻结train/holdout必需。训练排除holdout所有端点并记录标签/输入/model/training_ids哈希；无标签开发模型不能通过。评估拒绝泄漏、非实物输入、源key不符、产物哈希不符、缺失/重复xref及部署组件与得分不一致。基于完整标签左侧求TP/FP/FN/TN；无候选正例计FN，不inner-join丢失；计算0.5/0.7/0.8/0.9/0.95/0.97/0.99阈值曲线与难例分层，固定0.9门槛P≥0.97/R≥0.90，达不到exit1。**真实P/R及阈值曲线现在不可用，未声称PASS。**

实物待人工判定的高分否决例：COMMONWEALTH HEALTHCARE CORPORATION（MP同名，完整ID冲突，p=0.9999999995748103）；URSI TECHNOLOGIES INC（已知国家冲突，p=0.9999999999999885）；DEERE AND COMPANY（IL同名，上下文不足，p=0.9999458594141094）。这说明规则实际执行，不说明人工真值。用unique_id和pair_decisions/occurrences可逐行复查；真假/召回损失仍需人工审阅。

## 测试与Spark边界

TDD RED：实现前`pytest er/tests -q`因er.normalize/er.evaluate缺失返回2；证据red.log。GREEN：16 passed / 1 opt-in skipped（最终默认ER执行，2秒左右），覆盖规范化、同名异企、直接/传递强ID冲突、国家冲突、漏候选FN、缺标签拒绝、空/单条/无blocking/候选超限、真实Splink的明确合成u/EM/predict回归、固定split、fixture不可冒充实物，以及两个Spark协议/SQL合同测试。`er/tests/fixtures` 的合成truth只用于数学/管道，不参与真实质量验收；单位测试P=R=1不报告为实物指标。

`er/spark_connect.py`、`er/databricks_run.py`、`er/databricks/{notebook.py,job.json,README.md}` 提供真正Spark Connect/Splink后端路径与配置。另通过真实本地 Spark4.2.0 + Delta 的96条明确合成输入集成：实际seed42随机u（max_pairs10000、num_chunks1）、两轮EM、predict144对；1 passed in90.40s。清理5个剩余本run Delta对象并保留外部sentinel。小fixture存在未观测m层并使用Splink defaults的warning，仅证明执行路径与清理，不声称全参数训练或质量。真实实物v2模型untrained=[]是另一份证据。日志 `.tools/er/spark-live.log`、summary `.tools/er/spark-connect-integration.json`；初次真实执行暴露并修复Connect mode别名与Python聚合counts注册两处协议遗漏，RED日志另存。临时服务已停止，15123端口释放，token删除，`.tools/spark-connect-local/runtime.json=STOPPED_LOCAL_ONLY`。

已核对Splink5官方tag/API；Free仅Connect，原生SparkAPI的Databricks初始化会触发JVM/JAR，因此项目通过DatabaseAPI后端继承扩展契约实现native SQL/Delta生命周期，共享原生Levenshtein/exact。所有输入由Spark训练/打分，最终带硬限制的driver图约束不是分布式图聚类。无环境伪装/库monkeypatch/本地Spark冒充远端。清理只跟踪本run对象。**尚无Free账号/真实远端语义验证/全量执行证据，live门禁开放**；本地客户端import、合同测试及真实本地合成Connect集成都不关闭此门禁。细节及官方来源见Databricks README。

官方API来源：[Splink5 tag README](https://raw.githubusercontent.com/moj-analytical-services/splink/v5.0.0/README.md)、[训练API](https://moj-analytical-services.github.io/splink/api_docs/training.html)、[5.0发布](https://moj-analytical-services.github.io/splink/blog/2026/09/28/splink-500-released.html)。

## Review fix round1（本地代码修复，原始质量验收仍OPEN）

共享`er.lineage.validate_input`在训练、预测/复制、评估前核对records与occurrences对export manifest的SHA；训练及预测manifest冻结`input_occurrences_sha256`，Free driver同用该逻辑。仅换occurrences必拒绝，重写input manifest也不能改绑定的模型谱系；旧缺该字段的模型/预测metadata不兼容，必须重跑，未改原件或v2输入。`er.registry`同时供本地及Free publication driver，评估按同一个父registry精确SHA重建canonical ID，与落盘registry/xref核对；不再退回minSHA。异地评估可用`--previous-registry`指定父文件新路径，但字节SHA必须相同。非0.9曲线点若需身份迁移，会显式标REVIEW_REQUIRED，不能用未审迁移调参。

本轮真实同批回放：`fix-r1-model`真u/EM exit0，untrained=[]，model SHA仍为上列5e606...；`fix-r1-initial`显式初始化、`fix-r1-next`消费其registry均exit0，117236来源全部ID变化0，初始ID与旧v2 xref变化0，332792原始行复制完整，完整强ID冲突组件0。输入records/occurrences SHA、scores/xref SHA均保持上列值。初始registry SHA=`a9156df37f57036ce59290934336a595e251e7d15f7a8ff1d238174db6d850f6`，当前=`b2a44476e4bc0ceef8404e90f2b26e1aaeac3731aa5ac26645e73b29f6e4914f`，父SHA一致。训练wall6.25s/RSS395952KiB；初始预测6.81s/1294244KiB；继承预测7.52s/1336504KiB。详见`.tools/er/fix-r1-{train,initial,next}.log`与`fix-r1-verification.json`。这是同一实物输入的继承回放；新增小UID/新组件、merge、split、撤回/恢复/歧义、损坏registry及评估继承由明确合成生命周期回归覆盖，未伪称真实新增企业验收。

本轮完整ER：26 passed/2 opt-in skipped；原125全仓结果是前版历史记录，不作为本轮覆盖。数学测试已改名`test_synthetic_precision_recall_mathematics`；真实`test_evaluate_meets_thresholds`仅在显式flag时读真实产物和人工CSV，不skip缺件、不回退fixture。实际启用现有0标签执行：**1 failed in0.19s（ValueError: requires at least500...）**；CLI仍BLOCKED/exit2。标签0/难例0、真实P/R/曲线与Free全量仍OPEN。命令见`er/labels/README.md`；日志`fix-r1-human-gate.log`、`fix-r1-evaluation.json`。Spark adapter未改、没有重启服务；只验证共享registry/lineage与Free preflight/既有SQL合同。Minor1小Spark fixture defaults warning记录deferred，保持不具质量验收资格。

本轮磁盘实测项目约6.6GiB、ER产物约1.1GiB，低于18GB；没有新增常驻服务。

## 2026-10-25：显式 subagent 弱参照消费，不替换历史 human 门禁

用户已批准本轮子agent代替真人并明示来源。以上0人工标签/原human门禁仍是保留的历史事实，本轮使用独立 `er.agent_reference`，不再以等待真人阻塞，也不伪装人工或法律真值。原600候选、revision-2的503结论/97弃权/165已解析难例与原研究目录均冻结不改。固定seed42/threshold0.9和P≥0.97/R≥0.90目标不放宽。

新入口显式绑定标签、原候选/研究split副本、标签/input manifest及独立给定的两个SHA trust roots、实际records/occurrences、training IDs/model/scores/xref/registry及研究cross-manifest。按现有约束聚类和精确父registry规则在内存重建，仅一个冻结阈值，拒绝任意xref加新digest冒充身份来源；不重训/新predict/改threshold/features/写registry，不打开任何DuckDB、原archive或保护facts。补证行需与冻结primary-source/revision-audit语义一致，原件发布期/SHA/行号/URL继续可追溯，引用URL仅作为既存证据不发起网络请求。

新的全新私有`.tools`输出拒绝覆盖、symlink或来源别名。`report.json`始终披露subagent、human_evidence=false、human_acceptance_eligible=false、acceptance_eligible=false与research registry背景；含来源/model/artifact及consumer代码SHA。无效证据exit2；联合目标未达exit1/AGENT_REFERENCE_THRESHOLDS_NOT_MET，达到也只记AGENT_REFERENCE_THRESHOLDS_MET/exit0，不记human或全项目验收。确切参数见[标签入口说明](../../../er/labels/README.md)。

报告分别保留holdout已解析分母/全部弃权、全部端点排除与实际训练交集、最终簇及难例指标、缺直接分数统计；development段仅train，错误条目和理由有界，不含holdout逐对标签/错误或曲线，不用于本任务调参。source issue计数/冻结质量发现SHA及弱参照/占位税号/缺上下文的局限同时披露。

冻结事实回归目标仍为600候选、503结论/97弃权、165已解析难例；95个已解析holdout/13个弃权、209个排除端点/0实际训练交集；TP28/FP0/FN20/TN47，P1.0/R0.5833333333333334（难例39个，P1.0/R0.5）。原始召回失败不会因新增消费入口而改为通过。原生Databricks/Snowflake及原质量目标仍未验收；任何合成测试成功均不改变该事实。实现/RED-GREEN/资源和一次冻结实物消费结果以实现报告为准。

本次独立consumer实际完成核验，私有不可变报告为`.tools/er/agent-reference-consumer-20261025-r2/report.json`：固定0.9的约束簇得TP28/FP0/FN20/TN47，P1.0/R0.5833333333333334，返回预期质量失败exit1；难例39对TP18/FP0/FN18/TN3、P1.0/R0.5，直接分数缺失19对。600候选/503结论/97弃权/165已解析难例、95已解析holdout/13弃权/209端点排除/0实际训练交集全部回归一致，human与acceptance标志仍false。development仅train408结论/84弃权，不导出holdout逐对标签/错误；本任务没有基于这些诊断训练或选择模型。

实际执行3.625秒、50ms采样的完整进程树RSS峰值799440896字节（不是连续精确最大值），2e9/180秒上限未触发，进程收尾完成且无残留。50项明确合成回归及静态检查通过，另有新报告只读parity/权限/SHA/fixture清理核验；源Parquet/原模型/registry未被覆盖。最初两次exit2分别暴露合法三列研究split副本与annotation ASCII序列化合同的实现/fixture误设，保留全部失败、单独资源批准和修复证据；不能把失败归咎标签或删去。完整记录见实现报告及`docs/evidence/er-agent-reference-20261025/`。

记录哈希在完整15列actual record上按sorted-key/compact JSON且ASCII转义计算，12列source snapshot逐字段/类型与实际值一致，所有occurrence证据仍独立核对；未改registry原UTF8 content_hash。原context生成器缺失，编码由冻结1200端点（ASCII全部匹配、UTF8仅1199）及与原研究Git提交一致的normalize源码经验推断；这不等于原生成器源码已验收，也不以snapshot自证替代实际input hash。根磁盘预算仍含历史PG计费/保留Docker链等原限制。原native平台、真实人类/法律证据和整体质量/项目验收仍未完成。
