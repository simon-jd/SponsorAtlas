# Task5：维度与SCD2本地证据

2026-10-23本地（UTC 2026-10-24）。这是实际DuckDB/dbt执行，不是手写SCD历史。Snowflake仅静态parse；没有连接、创建账号、Azure资源、GPU或公开发布。Task3原始实物质量/BLS真实文件/Snowflake和Task4人工P/R、Databricks Free全量闸门仍OPEN；维度GREEN不代表这些闸门通过。

## 输入、正式参考与资源

消费已复审的 `.tools/staging/actual-files.duckdb` 和 Task4 `real-input-v2` / `fix-r1-model` / `fix-r1-next`，不改其文件。332792个来源occurrence：LCA FY2024_Q4 120897、FY2025_Q4 118580；USCIS FY2022 59983、FY2023 33332。LCA是Q4，USCIS是对应FY观测，不能混成同一年或全年LCA。xref 117236，未解析企业ID 8行原样保留；81469个暂定规范身份。源企业关联均用 `(source_system,source_employer_key)`，不按名称。

loader实际验证records/occurrences/model/xref/registry摘要、input_kind、registry父链重建、来源键/UID唯一非空、每个records有occurrence、staging/ER provenance逐行双向多重集相等及四个原件计数。现有数据库只接受同registry或其直接继承版本；重初始化、旧批、身份改写和无谱系数据库拒绝。撤回来源保留inactive身份。身份连续性不证明聚类匹配质量；概率只留在来源xref，不作为整簇置信度。

三份新参考从已有已核验缓存归档，无重复下载：SOC2018 `ade08af4…f4077`、July2023 CBSA `952c4b1e…53629`、Census2023 Gazetteer `919df59b…f0c8`。完整SHA、URL、字节数、seed摘要见 [dimension_references.json](../../../dbt/seeds/dimension_references.json)。原字节在 `data/reference/<vintage>/<sha256>/<official-stem>_<vintage>_<完整sha256>.<ext>`，basename携带真实参考版本与完整SHA，metadata显式保存`official_filename`/`archived_filename`。SOC2018与CBSA2023-07不伪装成财年。生成器验证SHA、精确worksheet/header/TSV reader、显式SOC父关系、ZIP与计数，可从该目录离线重放。SOC共1447节点（23 major/98 minor/459 broad/867 detailed），3222个2023县、1915县映射935 CBSA。2020县沿用已审查Task3官方seed及其来源合同。place/CT town资料没有用于推断城市或邮编所在县。

先测项目6.6GiB，计划只复制必要关系、不复制payload；完成后项目约6.8GiB，`.tools/dimensions` 143MiB（含原缓存、actual和最终受控回放库/日志），小于18GB份额。actual库69480448字节；最终回放库68694016字节。删除了本任务第一轮冗余回放数据库，保留其日志。loader实测峰值RSS 1249448KiB（约1.19GiB），耗时4.08秒；DuckDB每连接硬限4GB/1线程/2GB spill，实际显示3.7GiB/1/1.8GiB。无常驻服务。最终检查前后RAW库、records、occurrences、model、xref、registry及已审查ER数据库共7文件SHA/大小/mtime完全相同，证据在 `.tools/dimensions/protected-{before,after}.json`。

## 模型与历史语义

实际维度：`dim_employer=81469`、`dim_occupation=1447`、`dim_geography=6457`（2020县3235+2023县3222）、`dim_date=13149`（2000-01-01至2035-12-31）。所有代理键执行unique/not_null。日期含日/月/日历季度/年及10月1日开始的联邦财年/季度。

雇主按active、最新源财年、LCA优先、来源系统/来源key稳定选择属性，不依赖事实行顺序。dbt原生check snapshot捕获UTC微秒处理时间，`employer_key=dbt_scd_id`，`[valid_from,valid_to)`，current统一NULL终点；源观测财年/发布期、本地加载时刻和snapshot有效期分别保存。actual首版为 `2026-10-24T05:11:26.670598Z`。不倒填2022—2025业务属性日期，也不伪造源逐行抓取时间。所有239477条LCA事实早于首snapshot，显式为 `PRE_SNAPSHOT_HISTORY_UNKNOWN`；Task6须保留未知历史状态，按自然键和半开时间区间最多匹配一版本，不得仅套当前版本。此处诊断以LCA决定日期说明边界，Task6仍需按各事实明确的业务日期关联。

县参考版本与CBSA版本分列；同FIPS的2020县只对接**2023划界**，标 `CODE_CONTINUITY_TO_2023`，不能当作2020都会区历史。CT旧8县无可靠cousub证据，CBSA保持NULL。`OUTSIDE_2023_REFERENCE`区别于已在2023县参考中确认无CBSA的`NON_CBSA_2023`。纬经度明确是2023县内部点，绝非worksite坐标。

`int_dimension_mapping`保留239477条LCA，没有删坏行、猜标题、伪造企业或从城市/ZIP推县：

| 诊断 | 行数 |
|---|---:|
| SOC匹配 | 237515 |
| 标准化后不在SOC2018参考 | 1961 |
| 原SOC畸形 | 1 |
| 县匹配 | 131487 |
| 原县缺失 | 98769 |
| 原县未解析 | 9221 |
| 县同码对接2023 CBSA | 130372 |
| 已确认非CBSA（2023口径） | 811 |
| CT桥待补 | 292 |
| 不在2023县参考范围 | 12 |
| 因县未解析而无CBSA诊断 | 107990 |

未知SOC保留NULL occupation_key及原始码；这些是显式源异常，不伪装成维度映射成功。覆盖测试另行拒绝丢行、重复源行、已匹配县在维度中丢失、雇主维度丢失及未知诊断状态；原staging质量仍失败/OPEN。

## RED / GREEN和真实多轮snapshot

TDD RED：`.venv/bin/python -m pytest scripts/tests/test_dimensions.py -q` → `3 failed`，预期为两个新实现模块和SCD SQL尚不存在。原生dbt空选择默认返回0，不能冒称失败；改用 `dbt/run.sh --warn-error build --select +dim_employer` → exit2，`Compilation Error: selection criterion '+dim_employer' does not match any enabled nodes`。之后新增input_kind拒绝测试先得到 `1 failed, 11 passed`（`DID NOT RAISE`），补验证后转绿。

最终聚焦验证（未重跑旧root suite）：

```bash
.venv/bin/python -m scripts.build_dimension_references --sources data/reference
.venv/bin/python -m scripts.load_dimension_inputs --database .tools/dimensions/actual.duckdb
SPONSORATLAS_DUCKDB_PATH="$PWD/.tools/dimensions/actual.duckdb" DBT_LOG_PATH="$PWD/.tools/dimensions/logs" DBT_TARGET_PATH="$PWD/.tools/dimensions/target" dbt/run.sh --warn-error build --select +dim_employer +dim_occupation +dim_geography +dim_date +int_dimension_mapping
.venv/bin/python -m pytest scripts/tests/test_dimensions.py -q
.venv/bin/python -m scripts.dimension_checks --output .tools/dimensions/scd2-final
.venv/bin/ruff check scripts/build_dimension_references.py scripts/load_dimension_inputs.py scripts/dimension_checks.py scripts/tests/test_dimensions.py
dbt/run.sh --warn-error parse --target snowflake
```

结果：actual原生build `PASS=28 WARN=0 ERROR=0 SKIP=0 TOTAL=28`，包括4 seeds、1 snapshot、6模型、17数据测试；Python `14 passed`，无警告；Ruff `All checks passed!`；Snowflake adapter1.12.4静态parse exit0。完整本地快照检查16次原生dbt命令：9次成功、7次期望的数据测试失败，最后恢复6项测试GREEN。细节在 `.tools/dimensions/scd2-final/evidence.json`及逐步日志。

受控回放从真实必要字段副本首跑81469版本；只在该副本改一个企业展示名后snapshot为81470；重复snapshot、重排所有来源行再次snapshot仍81470。被改企业真实dbt区间为：

| 版本key | valid_from UTC | valid_to UTC | current |
|---|---|---|---|
| `7ab6ab0f02ab9eaa1a21aacd125f3fef` | 2026-10-24 05:18:11.177933 | 2026-10-24 05:18:15.885797 | false |
| `dc7299d29aa6f949f961ecbe74b026bf` | 2026-10-24 05:18:15.885797 | NULL | true |

这些是**受控本地变更验证**，不声称真实企业业务属性在该时刻变化；actual维度保持原始展示属性及81469首版本。测试另验证边界时刻只命中一个版本。7种破坏：重叠、双current、相同起点、逆区间、无current、重复代理键、NULL代理键，分别得到dbt exit1和明确`fail`；并非把编译失败算作拒绝。Python执行同一SCD SQL还覆盖长区间跨非相邻版本的重叠、零长度、current与NULL终点不一致及合法相邻半开区间。


## 审查修复round1：归档basename与无缓存重放

按审查要求，将三份归档basename同步改为`<official-stem>_<reference-vintage>_<完整SHA256>.<ext>`；父目录继续保留版本/SHA。官方原名分别在metadata的`official_filename`中保留，`archived_filename`记录新basename，`generator_version=2`。SOC采用真实`SOC2018`，CBSA采用`CBSA2023-07`，Gazetteer采用`Census2023`，没有虚构财年/季度；三份原件只改名，字节数和完整SHA均不变，三个CSV seed的SHA也全部保持不变。

参考测试只从已提交`data/reference`读原件，在无`.tools`的新临时工作目录运行，验证离线复制、重复重放和source/archive损坏拒绝；生成器默认来源也改为`data/reference`。聚焦`.venv/bin/python -m pytest scripts/tests/test_dimensions.py -k official_reference -q`得到`1 passed, 13 deselected`；完整`.venv/bin/python -m pytest scripts/tests/test_dimensions.py -q`得到`14 passed`，无警告。未改snapshot/SQL，未重复16轮dbt；本轮不改变前述实物、远程、人审或历史未知边界。
