# 实体解析运行环境

2026-10-24资源更新：Task4临时完整Spark服务环境已停止并按18GB预算移除其`.tools/spark-connect-local/venv`，保留JVM依赖、数据/sentinel和全部运行证据。1147个可读进程均无该venv引用，15123端口已释放；精确27包重建清单留在`.tools/spark-connect-local/retired-venv-requirements.txt`及同目录JSON。根环境的DuckDB2.0、Spark Connect薄客户端4.2仍可导入，Databricks Free真实验收仍待账号；此前本地集成结果不会因回收依赖被记成远端通过。

2026-10-23（本地日期）在Task3实现提交`d56d350`后，为Task4准备根项目`er`可选依赖组，精确固定`Splink==5.0.0`。版本来源为[PyPI发布元数据](https://pypi.org/pypi/splink/5.0.0/json)，wheel散列及传递依赖由`uv.lock`固定。

实际运行：

```bash
UV_CACHE_DIR=.tools/uv-cache uv sync --locked --extra dev --extra snowflake --extra ingestion --extra staging --extra er
.venv/bin/python -c 'from importlib.metadata import version; from splink import DuckDBAPI, Linker, SettingsCreator; print("Splink",version("splink"),"DuckDB",version("duckdb"))'
UV_CACHE_DIR=.tools/uv-cache uv pip check
```

结果：新增Splink 5.0.0与PyArrow 26.0.0，没有变更已有依赖版本；DuckDB仍为2.0.0。`DuckDBAPI`、`Linker`、`SettingsCreator`实际可导入，145个包依赖兼容。仅下载约54MiB包，没有安装本地Spark或下载模型，没有新增常驻服务或Azure资源。

这是安装与导入证据。EM训练、预测、人工标签指标、Databricks Spark全量均未由该检查验证，随Task4实现和独立审查另记。

Task4后续兼容性核对发现：Databricks Free Edition仅有serverless计算，而serverless仅支持Spark Connect API，原生Splink 5 SparkAPI的Databricks初始化会访问SparkContext/JVM。依据[Free Edition限制](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)、[serverless限制](https://docs.databricks.com/aws/en/compute/serverless/limitations)与固定版本源码，实现者正在通过Splink后端扩展契约适配原生SQL/Delta路径；未修改安装包或伪造运行环境，远端尚未验证。

为实际导入及适配器合同测试，根项目新增`er-spark`组：`pyspark-client==4.2.0`，PyPI元数据及散列已核验。实际安装仅新增该薄客户端与Zstandard 0.25.0，没有变更已有依赖版本，未下载450MB的原生Spark发行包或启动本地Spark服务器。

```bash
UV_CACHE_DIR=.tools/uv-cache uv sync --locked --extra dev --extra snowflake --extra ingestion --extra staging --extra er --extra er-spark
.venv/bin/python -c 'from pyspark.sql.dataframe import DataFrame; from pyspark.sql.connect.dataframe import DataFrame as ConnectDataFrame; from splink.backends.spark import SparkAPI; print(issubclass(ConnectDataFrame, DataFrame), SparkAPI.__name__)'
UV_CACHE_DIR=.tools/uv-cache uv pip check
```

实际输出`True SparkAPI`，147个包依赖兼容。此结果证明真实客户端类和Splink模块可导入，不证明已执行Spark查询。Databricks notebook应使用所选serverless环境自带的Databricks Connect，不用此本地测试客户端替换远端环境。
