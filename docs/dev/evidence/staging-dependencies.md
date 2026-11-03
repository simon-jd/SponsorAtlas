# Task 3 运行环境准备

日期：2026-10-23（本地PDT）。从干净检查点`e0ccfec`继续，Task1/2不重做；执行技能沿用Superpowers subagent-driven-development。Task3 dbt代码尚在实现，本记录只证明依赖环境。

根可选依赖组`staging`与`uv.lock`固定dbt-core1.12.6、dbt-snowflake1.12.4、dbt-duckdb1.11.0，解析出DuckDB2.0.0；143个包。安装命令：

```text
UV_CACHE_DIR=.tools/uv-cache uv lock
UV_CACHE_DIR=.tools/uv-cache uv sync --locked --extra dev --extra snowflake --extra ingestion --extra staging
```

均成功。dbt-core-experimental-parser2.0.8本地构建成功，下载和编译缓存在项目`.tools`。protobuf由7.36.2解析为6.33.6；Snowflake connector/cryptography版本未降级。`uv pip check --python .venv/bin/python`检查143包全部兼容。

`DBT_SEND_ANONYMOUS_USAGE_STATS=false DBT_LOG_PATH="$PWD/.tools/dbt-logs" .venv/bin/dbt --version`实际输出Core1.12.6、DuckDB适配器1.11.0、Snowflake适配器1.12.4，三者均为当前v1对应版本；CLI同时提示v2可用。本项目明确使用锁定v1 Core以配合指定适配器，不将版本查询当作模型执行证据。usage stats关闭，日志写项目内，未创建默认home目录。

依赖变更后执行`.venv/bin/python -m pytest warehouse/tests scripts/tests/test_board_manifest.py -q`：14passed、3live skipped，0warnings；验证protobuf/dbt新增依赖没有破坏相关Snowflake/CDK本地测试，不证明Task3构建。

主源版本信息来自[dbt Core PyPI](https://pypi.org/project/dbt-core/1.12.6/)、[Snowflake adapter PyPI](https://pypi.org/project/dbt-snowflake/1.12.4/)、[DuckDB adapter PyPI](https://pypi.org/project/dbt-duckdb/1.11.0/)及实际CLI；实施者还会核对对应官方API文档。

本轮开头真实reaper`--apply`返回group_missing，0创建/0删除；无云资源费用，预算台账仍$0。真实文件源访问与模型测试结果另记`staging.md`。
