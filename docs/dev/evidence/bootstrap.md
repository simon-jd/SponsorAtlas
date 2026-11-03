# 初始化与实施基线

日期：2026-10-23；目录 `/home/postedism/Desktop/SponsorAtlas`；分支 `implementation/sponsoratlas`。

初始文件仅包含 README、总项目说明、实现计划、Goal 和启动脚本，无实际实现和测试。已完整阅读三个入口文件；原始需求基线提交 `89007e0`；开发与验收台账提交 `a0392e2`。持久 Goal 复用已有 active 记录，未重设范围或完成条件。

使用执行技能：本机 `/home/postedism/.codex/.tmp/plugins/plugins/superpowers/skills/subagent-driven-development/SKILL.md`，配合 `using-git-worktrees` 与 `verification-before-completion`。在用户限定的项目目录内使用专门实现分支；`.git` 初始只读，本地分支和提交已通过自动审批执行。技能原脚本不可执行，因此复制到项目忽略目录 `.tools/sdd` 后使用；未修改技能源。完整预审矩阵与逐任务账本在 `.superpowers/sdd/4-sponsoratlas-data-frontend-plan/progress.md`。

环境实测：Python 3.12.13、pytest 8.4.2、python-hcl2 8.1.4、Node 20.19.5、Docker daemon 29.9.1。Docker报告32 CPU、132310061056字节内存；所在卷可用162GB。资源信息只代表本机能力，未运行Airbyte或任何云资源。`uv lock` 成功，锁定35个依赖包（含可选 Snowflake 依赖），开发venv已安装10个包。所有新工具/缓存限于本项目 `.tools`、`.venv`。

验证：`.venv/bin/pytest --version` 输出8.4.2；`git diff --check` 退出0。初始无测试，不宣称测试套件通过；任务测试将在各自证据中记录。

官方源预检：

- [DOL Performance Data](https://www.dol.gov/agencies/eta/foreign-labor/performance)：搜索目录可识别FY2024/FY2025季度文件；网页工具直接访问返回403，尚未下载或计数。
- [USCIS H-1B Employer Data Hub Files](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/h-1b-employer-data-hub-files)：网页工具直接访问返回403，不采用第三方镜像冒充官方原文。
- [BLS OEWS Tables](https://www.bls.gov/oes/tables.htm)：官方目录可读，含May2024/May2025发布与XLSX下载链接；尚未同步。
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)：官方文档确认公开GET jobs接口，不涉及投递或外发消息；后续需保留posting id与internal job id的区别。

本记录只证明初始化、版本与源可访问性预检，不证明Snowflake、Airbyte、BI或前端验收完成。

## Task 2 依赖与接口准备

增加可选 `ingestion` 依赖组并锁定：openpyxl3.1.5、PyYAML6.0.3、Airbyte CDK7.39.1、psycopg3.3.6。通过 PyPI 官方版本 JSON 核对CDK版本与Python支持。最初7.15.0导致resolver临时降级Snowflake/密码学包，已改为当前7.39.1并重新锁定，最终Snowflake connector保持4.8.0、cryptography50.0.2，未提交中间降级状态。`uv pip check --python .venv/bin/python` 检查104个包，全部兼容。依赖变动后重新运行 `pytest warehouse/tests -q`：7 passed、3 live skipped，0 failures。

普通 urllib HEAD再次预检DOL FY2024/FY2025 Q4 XLSX和BLS May2024 all ZIP，均为403；Greenhouse公共jobs接口200。尚无真实文件计数或Snowflake同步结论。已决定多财年RAW按发布期分表，采用携带原文SHA256/URL/行号的JSON payload保留原始列名，供后续staging显式映射和检测未知列。
