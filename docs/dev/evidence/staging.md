# Task3 staging 执行证据（2026-10-24 UTC / 2026-10-23 America/Los_Angeles）

## 2026-10-29：复核空地址 USCIS 行的排除（第七阶段）

`make staging-actual STAGING_EVIDENCE=docs/evidence/task03-uscis-release-20261029`，库 `.tools/staging/fy2025-full.duckdb`：**exit 0**，38 pass + 7 success，`staging_flag_limits` pass，`rows_reconcile` true（32 核，负载约 3.2–3.3，其他项目在跑）。原始输出：[task03-uscis-release-20261029/](../../evidence/task03-uscis-release-20261029/)。

补上的缺口：之前 `summary.json` 的逐发布对账只有 RAW 行和 staging 行，排除数只在按财年的另一张表里，也没有校验"发布 + 排除 = staging"。现在每个发布都列 `released_rows` 和 `excluded_rows`，`rows_reconcile` 要求 RAW = staging = 发布 + 排除，`excluded_rows` 单独汇总；`uscis_release` 同时给出门禁看到的份额和把排除行算进去后的份额（`scripts/tests/test_staging_actual_report.py`）。

| 发布 | RAW | staging | 发布 | 排除 | 发布后 state_unresolved | 算上排除行 | 上限 |
|---|---:|---:|---:|---:|---:|---:|---:|
| USCIS FY2022 | 59,983 | 59,983 | 59,976 | 7 | 9（0.02%） | 16（0.03%） | 5% |
| USCIS FY2023 | 33,332 | 33,332 | 30,857 | **2,475** | 6（0.02%） | 2,481（**7.44%**） | 5% |

FY2023 门禁通过是因为排除了这些行：算上它们就是 7.44%，超过 5%。上限没有改：`dbt/seeds/staging_flag_limits.csv` 最后一次修改是 428f6dc（2026-10-27），e8267fe 没碰它。被排除的 2,482 行全部是 `state_unresolved`，`employer_name_missing` 都为 false。下游：重跑后的 ER 输入里 USCIS 正好是 59,976 / 30,857 行（[end-to-end.json](../../evidence/task03-uscis-release-20261029/end-to-end.json)），维度和事实是从这份输入建的。

## 2026-10-28：空地址 USCIS 行不进入发布门禁

用户决定丢掉州、城市、邮编都空的 USCIS 行，不放宽 5% 上限。这些行仍留在 `stg_uscis`，`released=false`，RAW 行数仍然对得上。发布份额、之后的 ER 导出和维度装载都不计它们。

命令：`make staging-actual STAGING_EVIDENCE=docs/evidence/task03-staging-d2`，库 `.tools/staging/fy2025-full.duckdb`。结果 **exit 0**，38 pass + 7 success，`rows_reconcile` true。原始输出：task03-staging-d2/summary.json。

| 财年 | staging 行 | 空地址排除 | 发布行 | 发布后 state_unresolved | 占比 | 上限 |
|---|---:|---:|---:|---:|---:|---:|
| 2022 | 59983 | 7 | 59976 | 9 | 0.02% | 5% 通过 |
| 2023 | 33332 | 2475 | 30857 | 6 | 0.02% | 5% 通过 |

已建成的维度库和 ER 产物还是上一次导出的，里面仍有这些行。下一次导出才会去掉。

## 2026-10-27（第三阶段）：ZIP 补县（当前结果）

新增 Census2020 ZCTA→县对照：`tab20_zcta520_county20_natl.txt`，6,821,287 字节，
sha256 `3ed41278…aaaf`（Census 不发布校验值；固定在 `dbt/source_contracts/zcta_county.json`）。
`scripts/build_zcta_county_seed.py` 生成 `dbt/seeds/zcta_county_fips.csv`：27,359 个 ZCTA
（全部陆地在一个县 23,611；一个县占陆地面积 ≥ 90% 的 3,748）。规则在看真实结果之前定好：
取 `WORKSITE_POSTAL_CODE` 前 5 位（4 位补前导 0），县的州必须等于工作地州；
顺序为源县名 → ZIP → 城市。

命令：`make staging-actual`。结果：**exit 1**，44 个节点 43 通过，`staging_flag_limits` 失败 2 行。
四个来源 RAW = staging 行数不变（120897 / 118580 / 59983 / 33332）。原始输出：
task03-staging/（`build.log`、`run_results.json`、`summary.json`、`unresolved-fy2025.json`）。

| 来源 | 财年 | 标记 | 之前 | 现在 | 上限 |
|---|---|---|---:|---:|---:|
| LCA | 2024 | geo_unresolved | 1714（1.42%） | 56（0.05%） | 5% 通过 |
| LCA | 2025 | geo_unresolved | 32892（27.74%） | **6489（5.47%）** | 5% **超限** |
| USCIS | 2023 | state_unresolved | 2481（7.44%） | 2481（7.44%） | 5% **超限**（用户决定） |

县的来源：FY2024 源县名 113183、ZIP 7277（其中 ≥ 90% 规则 307）、城市 381；
FY2025 源县名 18304、ZIP 87673（其中 ≥ 90% 规则 6351）、城市 6114。
FY2025 剩下的 6489 行：6444 行的 ZIP 是跨县 ZCTA 且最大县不足 90%（或不是 ZCTA），
45 行 ZIP 在别的州。最多的是 TX 75024（Plano，Collin/Denton）1246、76262（Westlake）458、
75082 347、75034（Frisco）343、78641（Leander）251、NC 27560（Morrisville）240。
没有为达标而放宽 90% 规则或 5% 上限；要解决这些跨县 ZIP 需要街道地址地理编码，或用户接受更高上限。

## 2026-10-27：城市补县 + 显式标记与上限

命令：`make staging-actual`（真实 LCA FY2024_Q4/FY2025_Q4 + USCIS FY2022/FY2023；该库的 BLS/jobs 仍是 fixture）。
原始输出：task03-staging/（`build.log`、`run_results.json`、`summary.json`、`make-test.log`）。

结果：**exit 1**，44 个节点里 43 通过，唯一失败是 `staging_flag_limits`（2 行超限）。
原来的 4 个失败（county 非空、SOC 非空、USCIS 雇主名非空、staging_quality）已改成"保留 + 标记 + 上限"，
`staging_quality` 本身现在通过。四个来源 RAW 行数 = staging 行数（120897 / 118580 / 59983 / 33332）。

| 来源 | 财年 | 标记 | 标记行 / 总行 | 占比 | 上限 | 结果 |
|---|---|---|---:|---:|---:|---|
| LCA | 2024 | geo_unresolved | 1714 / 120897 | 1.42% | 5% | 通过 |
| LCA | 2025 | geo_unresolved | 32892 / 118580 | 27.74% | 5% | **超限** |
| LCA | 2024 | soc_unresolved | 0 / 120897 | 0.00% | 0.1% | 通过 |
| LCA | 2025 | soc_unresolved | 1 / 118580 | 0.00% | 0.1% | 通过 |
| USCIS | 2022 | employer_name_missing | 7 / 59983 | 0.01% | 0.1% | 通过 |
| USCIS | 2023 | employer_name_missing | 1 / 33332 | 0.00% | 0.1% | 通过 |
| USCIS | 2022 | state_unresolved | 16 / 59983 | 0.03% | 5% | 通过 |
| USCIS | 2023 | state_unresolved | 2481 / 33332 | 7.44% | 5% | **超限** |

上限在看到真实数据之前定好（`dbt/seeds/staging_flag_limits.csv`），没有为通过而调整。

县的来源（LCA）：FY2024 源县名 113183、城市补县 6000、未解析 1714；FY2025 源县名 18304、
城市补县 67384（其中 936 行用"建制市优先于同名 CDP"规则）、未解析 32892（源县名为空 32625、不匹配 267）。
FY2025 未解析的大头是跨县城市：New York 5433、Austin 2260、Dallas 2019、Plano 1747、Chicago 1577、
Atlanta 1507、Houston 1478。这些需要 ZIP→县对照表（Census 2020 ZCTA–county relationship file）才能解；
本次 `curl` 下载需要人工批准，未能取得。USCIS FY2023 未解析州的 2481 行里 2475 行州、城市、邮编全空，
任何对照表都补不了；要么接受更高上限，要么在下游排除，需要用户决定。

**已实现并验证本地 dbt 清洗与拒绝闸门；实际源质量未全绿，Snowflake 实仓验收待试用账号。** 没有创建资源、apply、push或发布。项目操作仅限SponsorAtlas；预算遵守内部 Azure 预算约束，Azure本任务花费$0。Task4/5可以基于明确的来源接口继续独立代码，不应把未通过的质量门禁或人工 BLS/jobs 写成实际验收。

## 审查修复 round1：当前结果（BASE d56d350）

I1改为先验证原始整数拼写，再转DECIMAL(38,0)，不与货币舍入共用；I2建库前验证四个发布期各一份，拒绝重复/缺失修订及无效hash元数据，官方表空底+事务加载；M1修正AK四个CITY AND BOROUGH短名并保留唯一性；M2全部Python连接统一4GB/1线程/2GB spill。

定向pytest实际 **11passed/54.41s**，覆盖13个新整数反例及原8个mutation，24个原生dbt进程；新增CLI测试断言重复/缺期退出非零且无数据库/COMPLETE输出。M1在实际数据额外解析2行；用现有RAW仅刷新一次seed+build，**exit1、22.95s、7模型成功/28测试通过/4 data fail/0运行错误/0跳过**：县107990、SOC1、USCIS空姓名8、quality110496。四原件332792行仍全部保留；没有下载/重载源或弱化真实闸。证据：fix_round1.json、当前实际构建。

以下初版执行过程保留为 **d56d350历史记录**（其中107992/110498、2项pytest、11次dbt等为当时结果）；当前修复结果以上段及更新后的机器证据为准。BLS实物、Snowflake实仓与真实数据质量全绿仍未满足。

## 固定运行时与本地测试

PyPI官方元数据核验并由父任务锁定：`dbt-core==1.12.6`、`dbt-snowflake==1.12.4`、`dbt-duckdb==1.11.0`；实际 `duckdb==2.0.0`、Snowflake connector4.8.0、Python3.12。主源：[dbt-core](https://pypi.org/project/dbt-core/1.12.6/)、[Snowflake adapter](https://pypi.org/project/dbt-snowflake/1.12.4/)、[DuckDB adapter](https://pypi.org/project/dbt-duckdb/1.11.0/)、[dbt unit tests](https://docs.getdbt.com/docs/build/unit-tests)、[dbt data tests](https://docs.getdbt.com/docs/build/data-tests?version=1.12)。没有手写替代dbt执行器。

RED实际先执行 `dbt build --project-dir dbt --profiles-dir dbt --select staging`，在宏尚未实现时返回1：原生 unit test `annualize_wage_exact_cases` 报 `'annualize_wage' is undefined`，`PASS=0 ERROR=1 SKIP=1 TOTAL=2`。此前启动发现并修正了重叠路径重复unit声明和本地相对数据库路径问题；它们不作为业务RED。完整命令及时间保存在 red.json，原日志 `.tools/staging/red.log`。

```bash
.venv/bin/python -m scripts.staging_checks --output .tools/staging/checks-acceptance
.venv/bin/python -m pytest scripts/tests/test_staging_local.py -q
```

验收脚本创建独立的 `SYNTHETIC_ONLY` 数据库，执行真实 dbt seed/build：7个模型（6个业务+1个宏测试调用模型）、7个seed、31个data test、1个原生unit test（8组案例）。首次及恢复后 `dbt build --select staging` 都 `PASS=39 ERROR=0`；中间8次mutation每次退出1且是data test FAIL，执行错误数为0：LCA/USCIS/BLS各增加未知原始JSON键、额外物理RAW列、未知工资单位、非法日期、非法SOC、未知县。日期/SOC/工资和县错误同时被质量/字段测试拦截。脚本使用finally恢复原始payload/物理列，最后重新构建全绿。机器证据保留所有11次命令、退出码及具体测试ID；本地完整日志和run_results在`.tools/staging/checks-acceptance/`。

unit要求六个案例均精确覆盖，另加未知单位和大小写错误；月薪8666.67换算104000.04使用DECIMAL。模型断言验证六位SOC和O*NET后缀统一为15-1252、ISO与美式日期、06075前导零、St Charles的点号/多空格规范化→29183，以及CDC tombstone过滤和Ashby isListed=false保留。pytest Task3两项通过；旧任务回归由父代理独立报告98通过/3项真实Snowflake测试跳过。

## 实际原件及完整本地载入

普通官方GET本轮可取得DOL和USCIS，不能继续沿用旧403结论。四份原件均以原始字节和完整JSONL存入 `data/raw/{lca,uscis}/FY.../`，文件名含release+原件SHA，旁有`.provenance.json`；不提交原件、RAW内容或真实联系人数据。可重放的URL、精确worksheet和SHA锁在 [verified_releases.json](../../../dbt/source_contracts/verified_releases.json)。

| 实际发布文件 | 完整非空源行 / 本地RAW / staging | 表头 | 实际范围 |
| --- | ---: | ---: | --- |
| LCA FY2024_Q4 | 120897 / 120897 / 120897 | 97列 | 决定日期2024-07-01至09-30 |
| LCA FY2025_Q4 | 118580 / 118580 / 118580 | 98列 | 决定日期2025-07-01至09-30 |
| USCIS FY2022 | 59983 / 59983 / 59983 | 11列 | 官网实际提供FY2022 |
| USCIS FY2023 | 33332 / 33332 / 33332 | 11列 | 官网实际提供FY2023 |

LCA XLSX ZIP完整性校验成功，独立读取XML非空行及完整JSONL计数一致；表尾分别有477934/445109个仅含格式的空行，因此绝不把工作表维度598832/563690直接减一作为记录数。独立计数记录最后非空物理行；0值计为数据，未slice。完整载入共332792行，所有来源行在staging保留，未做聚合、去重、抽样或删除来求绿。

DOL官方PDF布局实际可读并以pdftotext-layout及首页渲染核对，PDF报告期写全年，但下载文件实际数据只含7–9月，故仅声称这两个Q4文件的完整非空记录，不声称全年历史。FY2025实际多出`LAWFIRM_BUSINESS_FEIN`，195条年度LCA列映射逐项覆盖97+98个原始列，每个排除有理由。真实USCIS头是`Employer`，不是早期fixture假设的`Employer (Petitioner) Name`；已改用完整FY2022/23实际映射，未注册无URL的FY2024/25默认源。源Tax ID不是完整FEIN。

实际完整文件构建记录对应：

```bash
.venv/bin/python -m scripts.inspect_lca_sources --output .tools/staging/independent-source-counts.json
.venv/bin/python -m scripts.load_staging_files \
  --database .tools/staging/actual-files.duckdb --output .tools/staging/actual-files-counts.json
SPONSORATLAS_DUCKDB_PATH="$PWD/.tools/staging/actual-files.duckdb" dbt/run.sh seed
SPONSORATLAS_DUCKDB_PATH="$PWD/.tools/staging/actual-files.duckdb" dbt/run.sh build --select staging
```

最后一条**真实退出1**，25.14秒，7个模型成功、28项测试通过、4项data test失败、运行错误0、跳过0；控制台合计`PASS=35 ERROR=4 TOTAL=39`。`unmapped_columns` 对全部实物payload键/物理列通过，工资单位、日期和provenance通过。BLS/jobs在该数据库仍是人工fixture，不得把该运行称为全源实物验收。

## 真实质量问题与处理边界

| 门禁 | 实际失败数 | 原因 |
| --- | ---: | --- |
| LCA county_fips非空 | 107992 | FY2025原县字段空98769；非空但不匹配FY2024 7716、FY2025 1507 |
| LCA SOC非空 | 1 | 原始`17-21121.00`含多一位数字；没有猜测正确职业 |
| USCIS employer_name非空 | 8 | FY2022空7条、FY2023空1条 |
| staging_quality | 110498 | 上述LCA107993 + USCIS2505，含2497个无法产生州FIPS的记录和8个缺雇主名 |

最初县失败108695。检查原值后修正双方句点/撇号/多空格规范化，减少703条；`ST CHARLES`、`ST JOSEPH`、`ST JOHNS`等确定性格式遗漏已修，不把实现缺陷当源问题。剩余非空县典型值：MA `BOSTON CITY`1693、`CAMBRIDGE CITY`672等是城市填县，需要受审查的官方place→county桥；VA `FAIRFAX`1468、MO `ST LOUIS`578、MD `BALTIMORE`222存在县/独立市歧义；CT `HARTFORD CITY`536、`NEW HAVEN CITY`223还涉及2020县映射与新规划区版本。源`MIAMI-DADE (OLD DEF)`等旧名称也未凭名称猜当前FIPS。Census2020官方映射是明示版本，不冒充2024/25完整现行县和CBSA桥。

USCIS无法映射州：空2483、`XX`11、军邮`AE`2/`AP`1；军邮代码不等于地理州FIPS。这些是缺失/适用性/歧义混合问题，不能都称录入错误。保留原值和错误状态，等待补齐可靠来源/版本化映射或明确的受审查隔离政策。现有严格门禁保持失败，不偷偷过滤行来宣称全绿。

## 资源事故、自审与剩余真实门槛

最初按键扩展再GROUP BY完整payload在2GB内存OOM；已改为逐行、按年度seed标量映射投影，取消行扩展与聚合。随后DuckDB lateral json_each检查产生失控spill，观测临时目录约29GiB，瞬时超过18GB项目份额。这是本次本地资源违规；已立即Ctrl-C停止该任务并只删除其本项目临时目录。替换为`unnest(json_keys(payload))`后全量检查约1秒。现在profile固定4GB内存、1线程、2GB `max_temp_directory_size`硬限；最终完整构建无OOM，项目总占用约4.0GiB，无常驻服务或云资源。不能把内存上限误认为磁盘限额，硬spill闸已落实。

自审覆盖：年度映射无全局忽略；物理+原JSON键双闸；金额精度/非法单位/日期/SOC/FIPS拒绝；输出行数和原件溯源；实际缺陷不删行；无真实联系人数据进入Git；board雇主键和job键含义区分；CDC无企业ID时明确NULL；JSON/日期等适配器差异共享业务逻辑。任务接口见 [dbt README](../../../dbt/README.md)，Task4需board→法人注册表及正确来源属性，Task5需现行county/CBSA官方桥与未解析策略。

还未满足的真实门槛：

1. 四份实物的质量测试仍失败，需可靠映射/原始补充或明确审查的quarantine策略；不能弱化现有闸门。
2. BLS现行官方URL为`/oes/special-requests/oesm24all.zip`、`oesm25all.zip`，普通GET仍403；官方download.bls.gov四个metadata文件也403。仅保留显式fixture子集，完整头和实物构建未验。[访问记录](../../../dbt/source_contracts/bls-access.json)、[metadata访问](../../../dbt/source_contracts/bls-timeseries-access.json)。
3. `dbt/run.sh parse --target snowflake`实际通过，只是静态解析。真实Snowflake trial/account/key-pair、RAW同步、实仓seed/build、实际Airbyte字段类型/metadata及CDC端到端仍待；没有用DuckDB冒充Snowflake。
4. 两个LCA仅Q4、USCIS22/23是可证实覆盖，后续全财年回填需补实际发布期。试用后的Blob Parquet/DuckDB只读marts镜像仍属后续任务。
