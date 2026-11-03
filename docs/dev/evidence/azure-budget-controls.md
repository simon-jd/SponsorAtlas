# Azure 清理与预算执行证据

日期：2026-10-23（America/Los_Angeles）；真实清理时间：2026-10-24T02:42:22Z。
依据：内部 Azure 预算约束、计划 Spec 后的资源约束、内部预算台账（均未随仓库发布）。本轮创建资源 0 项，登记估算仍为 $0.00；没有把消费 API 的未知金额当作实际花费。

## 用法与边界

```bash
make                                    # 仅显示帮助，不调用 Azure
./scripts/azure_reaper.sh                 # 默认 dry-run，也会重新读取候选资源
./scripts/azure_reaper.sh --apply         # 清理已到期且身份复核通过的资源
make azure-reap                         # 上述 --apply
./scripts/azure_reaper.sh --down          # 预览整个项目组删除
make azure-down                         # 删除身份复核通过的固定整个项目组
```

资源组固定为 `rg-sponsoratlas-lab`，命令行不提供任意组或订阅覆盖。先读取当前 Enabled account 的 UUID，后续所有 Azure 命令显式传入该 `--subscription`。只查询本项目组，不查询或修改 `rg-mlinfra-westus`。`group exists=false` 在两种操作及 dry-run/apply 下均是幂等成功。

reaper 的资源 ID 必须完整落在当前订阅和固定资源组的 `/providers/...` 路径下；拒绝跨组、相似组前缀、跨订阅、路径跳转与无效 ID。资源必须同时满足 `project=sponsoratlas`、`owner=codex-sponsoratlas`、有效且不晚于当前时刻的 UTC TTL。TTL 接受完整秒级 ISO 时间及可选小数秒，时区限 `Z` 或 `+00:00`；日期、无时区和其他偏移均跳过。缺失标签、错误标签、未来 TTL 均记录跳过原因。

每个候选在 dry-run 和 apply 中都会执行 `az resource show`，复核 ID、两标签及 TTL，之后才允许删除。down 在删除前读取两次固定组的完整 ID 和 project/owner 标签；组身份不匹配则非零退出。down 的含义是主动删除整个已确认项目组，因此不要求组内资源已过期。

Azure CLI 使用 `subprocess.run` 参数数组、`shell=False` 和 JSON 查询。读取失败、无效 JSON、错误 JSON 顶层结构、删除失败均不会报告成功；资源级 CLI 错误会继续记录后续资源，并在结束返回非零。日志采用 JSON 行，提供每项删除结果或跳过原因；错误只记录操作和退出码，不回显 CLI 原始输出或凭证。

CLI 默认使用忽略目录 `.tools/azure`；允许传入的 `AZURE_CONFIG_DIR` 也必须解析到本项目 `.tools` 内。扩展目录固定为该配置目录下的 `extensions`，关闭 CLI 遥测，不修改 HOME。不创建 cron、自动化、订阅预算提醒或任何云资源。

## TDD 与验证

实现前先运行 executable fake-az 测试：39 失败、1 通过，失败原因是清理 wrapper 尚不存在。随后实现并补齐异常路径，初次 focused suite 共 49 通过；安全默认目标修正后为 52 通过。fake az 是独立可执行文件，逐次核对真实 subprocess argv 和调用顺序，验证显式订阅、固定组、无任意 scope 参数、带空格 ID 的单参数传递，以及配置目录/扩展目录/遥测/HOME 环境约定。

首次实现时的项目默认 pytest suite：83 通过、3 跳过、3 个已有 Airbyte experimental warnings。3 个跳过均为未授权运行的 Snowflake live grants 测试。另通过：针对新增 Python 文件的 Ruff、shell `bash -n`、`make -n azure-reap azure-down`、`git diff --check`。未为这次工具补充低价值预算估算器；实际资源创建前仍必须逐项查询 Azure Retail Prices API、登记完整计费组件并检查累计估算 ≤ $5.40（总上限 $6）。

## Makefile 默认目标修正

父任务审查发现首个目标为 `azure-reap`，裸 `make` 会隐式执行 apply。新增真实 `make -n` 测试先复现失败：默认输出 `./scripts/azure_reaper.sh --apply`；两个显式目标测试通过。随后添加 `.DEFAULT_GOAL := help`，裸 `make` 只显示帮助，测试同时约束默认输出不得包含 `--apply`、`delete` 或 wrapper 调用。显式 `azure-reap`、`azure-down` 的命令保持不变。

修正后 focused suite 52 通过，Ruff 与 `git diff --check` 通过；真实裸 `make` 输出如下，不调用 Azure。此次补丁不重复全套测试，由父任务最终统一运行。

```text
$ make
Azure cleanup commands:
make azure-reap: clean expired project resources
make azure-down: remove the verified project resource group
$ make -n azure-reap azure-down
./scripts/azure_reaper.sh --apply
./scripts/azure_reaper.sh --down --apply
```

## 真实执行

在已登录的本项目本地 Azure CLI 配置下执行：

```text
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-10-24T02:42:22Z
$ ./scripts/azure_reaper.sh --apply
{"event": "cleanup_start", "group": "rg-sponsoratlas-lab", "mode": "apply", "operation": "reap"}
{"event": "group_missing", "group": "rg-sponsoratlas-lab"}
exit=0
```

本次真实路径只进行了 account show 和固定范围的 group exists；没有资源删除调用。不会为验证删除而创建收费资源。真正存在资源时的删除、标签变更、错误路径由 fake-az 测试验证，尚无真实资源删除验收。会话结束时父任务将按约束再执行一次 reaper。

局限：Azure CLI 的 show 和 delete 之间没有跨命令的原子标签条件；实现通过紧邻删除的重新读取缩小竞争窗口，无法消除外部并发修改。CLI 超时或非零返回时记录错误，不推断云端删除已完成。
