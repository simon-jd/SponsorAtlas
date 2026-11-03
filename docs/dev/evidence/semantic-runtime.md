# Cube 运行时预备验证

2026-10-24。此记录仅覆盖隔离依赖与一个明确合成的 Parquet 兼容性探针；尚未实现 Cube 模型或启动 REST 服务，不替代 Task 7 验收。

原有 Node 为20.19.5、npm为10.8.2。项目内`.tools/cube-runtime`安装精确版本`@cubejs-backend/server`、`duckdb-driver`、`snowflake-driver`均为1.8.0，681个包安装成功、exit0。缓存固定在本项目`.tools/npm-cache`。首次安装出现部分Google传递依赖要求Node≥22的`EBADENGINE`及上游弃用提示，保留于`install.log`；未将其描述为无警告输出。为解决实际引擎约束，从[Node官方发布目录](https://nodejs.org/dist/v24.22.1/)取得项目内Node24.22.1，压缩包32026916字节、SHA256为`6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6`，与官方SHASUMS一致后解压；没有改系统Node或全局包。正式语义层仍需提交自己的依赖清单和锁文件，并使用兼容Node运行。

选定版本依据为[官方1.8发布说明](https://cube.dev/blog/cube-core-v1-8-mistake-proof-defaults-performance)及[固定版本driver源码](https://github.com/cube-js/cube/blob/v1.8.0/packages/cubejs-duckdb-driver/src/DuckDBDriver.ts)。实际npm registry的部分`latest`标签仍指向1.6.75，因此没有使用浮动标签安装。1.8的Node要求为≥20，配置使用`cube.js`或`cube.py`；此前`cube.ts`已移除。

真实探针命令为`node .tools/cube-runtime/probe.cjs`；随后用项目内Node24.22.1重跑，均exit0。Python DuckDB2.0.0导出两行合成Parquet（372字节，文件设为0444）；Cube1.8.0的实际DuckDB引擎返回`v1.5.5`，读取行数2、DECIMAL合计300.3、DATE返回2026-10-24。探针显式限制512MB内存、1线程、256MB临时空间，`finally`释放连接。没有启动服务或访问Snowflake。它证明这个小样本的跨版本Parquet读取可行，未证明原生DuckDB2.0文件兼容或全部marts类型已经验证。

项目内Node24.22.1逐一检查实际安装的681个包的`engines.node`，不兼容数0，结果存于`.tools/cube-runtime/engine-check.json`。首次Node20安装警告作为历史输出保留；后续正式构建应使用兼容Node，不能仅凭Cube自身声明≥20忽略传递依赖要求。

后续必须通过真正Cube REST验证全部七项指标、视图和预聚合，并分别对账fixture与真实事实。源marts只读与可写scratch/preaggregation需要明确实现和拒绝写入验证；driver的`readOnly()`返回false，单独设置数据库路径不构成只读保证。精确工资分位不从分组分位平均；BLS官方中位数也不能跨不同benchmark取均值。研究接口、原始registry元数据和探针保存在本计划忽略工作区及`.tools/cube-research-20261024`中。

本轮未开通Azure资源，累计登记估算仍$0；隔离依赖和缓存计入项目18GB份额。没有GPU任务或新增常驻服务。
