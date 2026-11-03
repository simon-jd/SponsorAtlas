# 维度输入资料核验

2026-10-23（本地日期）为Task5准备官方分类与地理映射。全部普通GET成功、原字节已保存于项目`.tools/dimensions/sources/`并计算SHA256；XLSX/ZIP完整性检查通过。本证据不表示维度已实现或dbt已构建，后续生成seed前仍需按版本与hash归档原件。

| 原件与官方来源 | 字节 | SHA256 | 实际结构核验 |
|---|---:|---|---|
| [SOC 2018最终层级表（Census保存的BLS资料）](https://www2.census.gov/programs-surveys/demo/guidance/industry-occupation/soc_structure_2018.xlsx) | 51367 | ade08af40923266f3a854842e888ca3e93c15b26a147c20a2b12a61f4c4f4077 | `2018 Structure`，第8行表头，23 major / 98 minor / 459 broad / 867 detailed |
| [July 2023 CBSA delineation](https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx) | 143798 | 952c4b1e78acbb54e6ec9412434b7602fedacbf021736351a63c181bdb753629 | `List 1`，第3行表头，1915个唯一县键、935个CBSA；2行来源/说明尾注 |
| [2023 Counties Gazetteer](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip) | 141650 | 919df59ba90759cce85468c0337e898e4b39c08eaffce86ddd88fa41f1f7f0c8 | 单成员TSV，3222个唯一GEOID，含CT九规划区；经度表头有尾空格 |
| [Census2020 place-by-county](https://www2.census.gov/geo/docs/reference/codes2020/national_place_by_county2020.txt) | 2736928 | 9996494d33cf6ee4527491508aa1b8dae85dcff3a979886e19b0b375e5d06ec6 | pipe分隔、33618条记录，保留州/县/PLACEFP；place可跨县 |
| [Connecticut旧县—县分区—新规划区](https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.txt) | 22091 | 13ad002564e30e6dcd1df112fdaa985f2cd0cdb908f7f3017cd2723db294cff8 | 含引号内换行表头；174数据行=169 town+5未定义分区，另9条尾注 |

来源目录及含义分别由[Census划界资料页](https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html)、[SOC文件目录](https://www2.census.gov/programs-surveys/demo/guidance/industry-occupation/)、[2023 Gazetteer页](https://www.census.gov/geographies/reference-files/2023/geo/gazetter-file.html)、[2020代码目录](https://www2.census.gov/geo/docs/reference/codes2020/)和[县变化页](https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes/2020.html)核对。SOC使用下划线命名的最终版，未用目录里较早的同名连字符版本。

这些资料解除的是维度参考资料访问缺口，**不解除BLS工资ZIP的403、真实staging质量失败或任何云端验收**。当前staging县映射仍受Census2020版本约束，不能把旧CT县直接一对一改名为新规划区；CBSA表也不包含所有县，县无CBSA可以合法成立。映射必须保留vintage与匹配状态。

place/town对照不证明某个邮寄地址位于该行政边界。不得仅凭邮寄城市名/ZIP推断精确县，更不能把县内部点当成工作地点坐标。真正地点解析需可靠行政区ID或地址地理编码的证据；不确定记录继续显式保留。
