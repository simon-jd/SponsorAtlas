# Snowflake 基座：本地证据与待验收项

日期：2026-10-23；2026-10-30 更新。状态为 **已在 Snowflake 试用账号上 apply 并通过在线权限测试**（见文末“2026-10-30 在线验证”）。2026-10-23 时没有运行 Terraform apply，下面“已实现”到“最终检查”记录的是当时的离线结果。需求起点 `a0392e2`；父任务补充文档基线 `1cdfabe`。

## 已实现

五个要求的 Terraform 文件包含真实 provider resources：RAW 与 ANALYTICS 数据库、七个 managed access schema、四个功能角色、XS warehouse、三名服务用户、角色绑定、当前/未来对象 grants，以及 KEYPAIR-only authentication policy 和三个用户绑定。没有密码字段、业务角色继承或账号级 grant；部署 provider 显式 `SNOWFLAKE_JWT`。准确对象及凭证接口见 [warehouse README](../../../warehouse/README.md)。

PII_READER 此处只建立角色接口；模型级 masking policy 及授权人工身份属于后续治理实现，当前没有真实未掩码可见性的证据。

## 版本与来源

| 组件 | 本地版本 / 证据 |
|---|---|
| Python / 项目 | `.venv` Python 3.12；editable sponsoratlas 0.1.0 |
| pytest / Ruff / python-hcl2 | 8.4.2 / 0.17.2 / 8.1.4 |
| snowflake-connector-python | 4.8.0，来自既有 `uv.lock`；`uv sync --locked --extra dev --extra snowflake`，未修改 pyproject/lock |
| Terraform CLI | 1.13.5 linux_amd64，`.tools/terraform/terraform` |
| Terraform ZIP sha256 | `0dbe3fcc268eb670801af6a6456799d1ae26e72e73797f6c6167e18aafd1fd9a`，对照 HashiCorp SHA256SUMS 校验 |
| Snowflake provider | 官方 `snowflakedb/snowflake = 2.22.0`；init 验证 partner 签名，key ID `FF482FB9150C970F`；提交锁文件 |

2026-10-23 查询官方资料后固定版本；实现按对应 tag 文档核对字段，并以本地 provider schema validate 复核。[Snowflake 官方 provider 发布记录](https://github.com/snowflakedb/terraform-provider-snowflake/releases/tag/v2.22.0)、[service_user](https://github.com/snowflakedb/terraform-provider-snowflake/blob/v2.22.0/docs/resources/service_user.md)、[grant resource](https://github.com/snowflakedb/terraform-provider-snowflake/blob/v2.22.0/docs/resources/grant_privileges_to_account_role.md)、[authentication policy](https://github.com/snowflakedb/terraform-provider-snowflake/blob/v2.22.0/docs/resources/authentication_policy.md) 与 [用户绑定](https://github.com/snowflakedb/terraform-provider-snowflake/blob/v2.22.0/docs/resources/user_authentication_policy_attachment.md) 支持使用的 API。

本地 mock 的能力边界按 [HashiCorp mock provider 文档](https://developer.hashicorp.com/terraform/language/tests/mocking)；在线连接按 [Snowflake Python key-pair 文档](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect#using-key-pair-authentication-and-key-pair-rotation)。这些文档不构成项目已部署的证据。

## RED → GREEN

先创建离线安全契约测试，尚无 Terraform 文件时运行：

```text
.venv/bin/python -m pytest warehouse/tests/test_terraform_contracts.py -q
exit 1
AssertionError: Snowflake Terraform implementation is missing
5 errors in 0.05s
```

实现后第一轮 pytest 为 `5 passed, 3 skipped in 0.09s`，追加 provider 锁定及 grant 资源连接检查后为 `7 passed, 3 skipped in 0.09s`。三个 skip 都为 `set RUN_SNOWFLAKE_GRANTS=1 after nonproduction write/cost authorization`。这是离线契约的 RED/GREEN；**不是原始云端权限断言的 RED/GREEN**。没有凭证和费用授权，不能声称真实数据库先失败再通过。

实现过程中还修正了 python-hcl2 8 保留字面引号的解析差异、Terraform 函数拼写，以及 mock provider 缺少可选默认值引起的空值断言。最终 grant option 显式 false；`all_privileges` 不配置，因为 provider 会将其与显式 `privileges` 视为冲突。没有通过 mock 填充假云端结果。

## 最终检查

均在项目根运行；工具下载、uv 缓存、provider 缓存及 Terraform 工作目录均位于本项目。

| 命令 | 结果 |
|---|---|
| `UV_CACHE_DIR="$PWD/.tools/uv-cache" uv sync --locked --extra dev --extra snowflake` | exit 0；补齐 24 个已锁定的可选依赖 |
| `TF_PLUGIN_CACHE_DIR="$PWD/.tools/cache" CHECKPOINT_DISABLE=1 .tools/terraform/terraform -chdir=warehouse/terraform init -backend=false -input=false` | exit 0；官方 provider 2.22.0 安装、签名验证、生成锁文件 |
| `.tools/terraform/terraform -chdir=warehouse/terraform fmt -check -recursive` | exit 0 |
| `.tools/terraform/terraform -chdir=warehouse/terraform validate -no-color` | exit 0；`Success! The configuration is valid.` |
| `CHECKPOINT_DISABLE=1 .tools/terraform/terraform -chdir=warehouse/terraform test -no-color` | exit 0；mock provider，仅 plan；4 passed, 0 failed |
| `.venv/bin/python -m pytest warehouse/tests -q` | exit 0；7 passed，3 live skipped |
| `.venv/bin/python -m pytest -q` | exit 0；7 passed，3 live skipped |
| `.venv/bin/python -m ruff check warehouse/tests` | exit 0；All checks passed |
| `git diff --check` | exit 0 |

四个 Terraform 测试检查展开后的对象数量、XS/60 秒、schema 和当前/未来对象 grant、服务用户绑定与 KEYPAIR 策略，并拒绝缺失公钥、额外身份、私钥 PEM 输入。七个 pytest 离线测试通过资源类型白名单和完整权限矩阵防止增加越权路径；云端正向/反向对照不以任意异常冒充权限拒绝。

## 2026-10-30 在线验证

账号：一个 Snowflake 试用账号（Enterprise 功能可用），账号标识和密钥只通过环境变量传入（`SNOWFLAKE_TEST_ACCOUNT`、`SNOWFLAKE_TEST_{ADMIN,LOADER,TRANSFORMER,ANALYST}_PRIVATE_KEY_FILE`），不进仓库。用户和协调者在 2026-10-30 以 `native_phase=READY` apply 了全部 `warehouse/terraform`（Terraform state 为 22 个对象加 69 个授权与绑定，共 91 个实例；READY 之后的 plan 无变更）；Terraform state 为本地文件（用户接受，替代 Azure Blob）。成本上限是 5 credit、到期不重置的资源监视器（80% 挂起、100% 立即挂起）。

| 检查 | 结果 |
|---|---|
| `RUN_SNOWFLAKE_GRANTS=1 pytest warehouse/tests/test_grants.py`（ADMIN = 部署身份） | 3/3 PASS：ANALYST 读不到 RAW，能读 MARTS 表和视图但不能写，TRANSFORMER 能读 RAW 但不能写 |
| `warehouse/tests/test_live_marts_access.py`（真实 dbt 表） | 3/3 PASS：CUBE_SVC 能读全部 7 张 marts（行数与本地一致），读 RAW、STAGING、INTERMEDIATE（含 snapshot）均被拒 |
| 策略挂载 | 默认构建上 `APPLICATION_OWNER_V1`、`CONTACT_EMAIL_V1` 的 `POLICY_REFERENCES` 为空：代码里唯一的挂载点是 `fact_my_applications`（`private_applications` 物化，默认 `enable_private_applications=false`，且 RAW 里的 CDC 夹具没有 v2 字段）。TRANSFORMER 用同一个 dbt 宏建 UUID 探针表时，行访问策略、掩码策略和 tag 在一条 CREATE 里挂上（对象级 APPLY 足够）；ANALYST 和 TRANSFORMER 都看到 0 行；只带掩码策略的探针表上，没有 PII_READER 的 ANALYST 看到 `***MASKED***` |

输出：[pytest-live.log](../../evidence/task01-snowflake-grants-20261030/pytest-live.log)。

仍未验证：owner 角色看到行、PII_READER 看到明文（需要 `governance/snowflake_acceptance.py` 的四个专用身份和一个 owner 角色，SQL 由人工以 ACCOUNTADMIN 执行，尚未创建）；外部 PUBLIC/角色继承漂移检查。

## 2026-10-31 在线验证（phase 9）

同一个 Snowflake 试用账号，账号和密钥仍只通过环境变量传入。资源监视器额度已由用户从 5 调到 20 credit（80% 挂起、100% 立即挂起，2026-11-24 00:00 UTC 结束）。

| 检查 | 结果 |
|---|---|
| `RUN_SNOWFLAKE_POLICIES=1 pytest warehouse/tests/test_policies.py`（四个 `SPONSORATLAS_POLICY_*` 用户，角色 ANALYST；owner 角色 `SPONSORATLAS_OWNER_POLICY_TEST`；ADMIN = 部署身份；TRANSFORMER = DBT_SVC） | 5/5 PASS，23.45 s：owner 无 PII_READER 看到行、邮箱为 `***MASKED***`；owner + PII_READER 看到明文；NON_OWNER、PII_NON_OWNER、TRANSFORMER 看到 0 行；APPLY 授权限于对象。测试表已删除（事后 SHOW 为空） |
| `RUN_SNOWFLAKE_GRANTS=1`：`test_grants.py` + `test_live_marts_access.py` + 新增 `test_live_account_defaults.py` | 8/8 PASS，53.37 s：AIRBYTE_SVC、DBT_SVC、CUBE_SVC 各自只看到 `SPONSORATLAS_XS`，`CURRENT_AVAILABLE_ROLES()` 只有本角色和 PUBLIC |
| 试用账号默认授予 PUBLIC 的对象 | 用户已撤销 `SNOWFLAKE_LEARNING_ROLE`（含无监视器的 `SNOWFLAKE_LEARNING_WH`）和 `SYSTEM$STREAMLIT_NOTEBOOK_WH` 的 USAGE；这是 Snowflake 自建的授权，不在 Terraform 中，记录在 `warehouse/README.md` 的 Manual setup 一节 |
| 结束时仓库状态（SHOW WAREHOUSES） | X-Small、SUSPENDED、auto_suspend 60、单集群、监视器 `SPONSORATLAS_BOOTSTRAP_RM` |

输出：[policies](../../evidence/task08-snowflake-policies-20261031/)、[grants](../../evidence/task01-snowflake-grants-20261031/)、[cost run log](../../evidence/task10-snowflake-cost-20261031/run.log)。

未完成（2026-11-01 已补跑，见下节）：Task 10 成本实验。`query_profile_report.py` 在连接之前的准入检查就拒绝了：密钥文件权限为 0640（应为 0600，脚本按设计拒绝组可读的私钥）。没有发出任何 Snowflake 语句，没有 TASK10 schema 或 clustering key。策略和成本身份由用户在 Terraform 之外创建，本阶段之后由用户删除。

## 2026-11-01 成本实验（Task 10，协调者补跑）

用户同意后，密钥文件恢复为仅属主可读（文件 0600，目录 0700）。第一次运行（07:29 UTC）在准入检查被拒：脚本读的是 `SUSPEND_IMMEDIATE_AT`，而 `SHOW RESOURCE MONITORS` 返回的列名是 `suspend_immediately_at`，测试夹具也用了同样的错误列名。已修正 `scripts/query_profile_report.py` 和夹具，并加了回归测试（只有旧列名时必须拒绝）。这一次只执行了 SHOW。

第二次运行 07:31:56–07:32:58 UTC，身份 COST_EXPERIMENT_SVC，表为 FY2025/CA 事实表的临时副本（8 个 micro-partition）。主机 nproc 32，负载 14.06，其他项目的任务同时在跑。每个阶段 3 条查询，加一次 `cost-refresh.cjs` 的 Cube 刷新：

| 阶段 | 3 条查询耗时 | Cube 刷新 | 归因 credit（8 h 后） |
|---|---|---|---|
| X-Small 基线 | 0.51 s | 6.40 s | 0.0（1 条无记录） |
| 聚类键 (fiscal_year, worksite_state) | 0.38 s | 4.51 s | 0.0（1 条无记录） |
| Small | 1.32 s | 5.34 s | 0.0031（1 条无记录） |
| 结果缓存首次 | 0.41 s | 4.25 s | 0.0（1 条无记录） |
| 结果缓存重复 | 0.38 s | 3.62 s | 3 条均无记录 |

恢复检查 VERIFIED：X-Small、auto_suspend 60，聚类键和临时 schema 已删除。

ACCOUNT_USAGE 对账在运行后 4.7 h 和 8.1 h 各跑一次，两份结果逐字节相同，状态仍为 `MEASURED_ATTRIBUTION_PENDING`。X-Small 查询每条约 0.1–0.2 s，归因为 0 或没有记录；扫描 0 字节的查询也没有记录。整个实验小时仓库计量 0.0417 compute + 0.0040 cloud services credit，其中 0.0181 归因到查询，其余是空闲和未归因的短查询。自动聚类没有运行过（AUTOMATIC_CLUSTERING_HISTORY 为空）。top-20 报告（COST_OBSERVER_SVC）：20 条带标签的查询，6 条有归因，最大 0.0079 credit。

结论：8 个 micro-partition 的表太小，聚类和规格的差别都在噪声里；要得到有意义的前后对比，需要更大的表。资源监视器：运行前 0.69，运行后 0.73，16:05 UTC 为 0.81（含之后的 top-20 和对账查询）。

清理：2026-11-01 用户删除了 4 个策略测试用户、owner 角色和 COST_EXPERIMENT_SVC / COST_EXPERIMENTER。再用它们的密钥登录都被拒绝，对应的密钥文件也已删除。COST_OBSERVER_SVC 保留，以后出成本报告还要用。

输出：[experiment](../../evidence/task10-snowflake-cost-20261031/experiment-P9A.json)、[top20](../../evidence/task10-snowflake-cost-20261031/top20-remote.json)、[reconciled 8h](../../evidence/task10-snowflake-cost-20261031/experiment-P9A-reconciled-8h.json)、[run log](../../evidence/task10-snowflake-cost-20261031/run.log)。
