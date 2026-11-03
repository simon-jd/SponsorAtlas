# BLS May2025 原件与JSONL核验（尚未入RAW）

2026-10-24。已从[BLS官方表格页](https://www.bls.gov/oes/tables.htm)的[May2025 All data ZIP](https://www.bls.gov/oes/special-requests/oesm25all.zip)通过正常Chrome下载取得完整原字节；常规Python GET对2024/2025仍403。下载工具未遵守设定的45秒超时，实际长时间阻塞；不重复该路径。工具默认保存到Downloads，本代理只核验并移动这一个新下载文件至项目，未操作其他下载，外部副本已移除。

- ZIP：78,754,064字节，SHA256 `86ffcbbeb27a96b7aa84d01801de71ee94ee9027452152caabebfdec9d764fde`，ZIP CRC完整性通过。
- 成员：`oesm25all/all_data_M_2025.xlsx`，80,657,033字节；仅此数据XLSX和外层目录，无选错子文件。
- 数据sheet：`All May 2025 data`；另有`Field Descriptions`及两个隐藏元数据sheet，不把它们计入数据。
- openpyxl独立读取首行32字段；ZIP/XML流式逐行核验413527条非空数据、0空数据行，与声明范围`A1:AF413528`减表头一致，22.57秒。没有从文件名或格式化行数直接推断计数。

原件已用现有`archive_release`正式归档至`data/raw/bls/FY2025/BLS_FY2025__86ffcbbeb27a96b7aa84d01801de71ee94ee9027452152caabebfdec9d764fde.zip`，同stem JSONL为413527行，与独立XML计数相同。JSONL SHA256为`56ea8a7aa33e1913c332e7d28d9f06742a408e746fb33c9a3fb5f383ced31dfa`；现有reader明确选择实际成员和sheet，无公式/空行混入。归档36.12秒、峰值RSS254656KiB、exit0，原件SHA不变；registry/source/counts/log在`.tools/bls-access-20261024/`。现在尚未入RAW、年度32列映射或dbt build；**不得把归档写成Task2/3 Airbyte/Snowflake管道已验收**。May2024原件仍缺，本次未声明两个发布期都已取得。

## 后续事实grain必须处理的真实差异

原件同时有全国、州、领地、都会区和非都会区，`AREA_TYPE`分别为1/2/3/4/6，AREA不是统一县FIPS。全国177407、州36396、领地1012、都会区150023、非都会区48689行。

有行业和所有制切片；不把所有行直接映射为唯一SOC×地区×年份。`I_GROUP=cross-industry`、`NAICS=000000`（实际值类型需由reader保留/显式处理）、`OWN_CODE=1235`共237521行；其中`O_GROUP=detailed`为223555行，SOC×AREA_TYPE×AREA无重复。不限制职业层级时，同一代码/地区有7个重复grain；原件Field Descriptions明确说明一些OEWS聚合职业会分别以broad和detailed出现。详细职业子集是可复核的一种视角；不能仅为消除重复而丢掉所有官方大类/中类/宽类基准。进一步逐行比较发现这7对全国记录除O_GROUP=broad/detailed外其余31列完全相同，保留所有官方职业层级并规范这7对重复后，全行业/全所有制有237514个唯一grain。完整原件仍保留，canonical偏好与重复来源行应明确记录；新输入若数值冲突必须报错，不能任意去重。

逐行核验得到1394个职业码，其中830个在源中标detailed。6个不在当前SOC2018参考：总计00-0000，以及21-1018、25-2052、25-9045、51-2028、53-1047这5个OEWS特定详细职业码；它们必须按源分类与版本表达，不能改写成别的标准SOC。原件说明指出OEWS特定组合职业码可能不同于标准SOC2018详细职业；后续与dim_occupation关联应保留分类命名空间，不猜修码或标题。保留行业/所有制/职业层级与May日历调查年，不把归档FY标签当调查日期。

`*`、`**`、`#`是源缺失/抑制/顶码状态，不能当普通数值或补零。实际TOT_EMP的`**`有16060行；A_MEDIAN的`*`有5073行、`#`363行。Field Descriptions还含历史顶码说明，不能仅据该文字把所有数值裁到固定上限或制造精确工资。不同年份OEWS也不能直接当严格可比的工资增长时间序列，参见[BLS使用说明](https://www.bls.gov/oes/oes_ques.htm)。

完整32字段及分组/抑制计数见`headers.json`与`independent-counts.json`，7个重复的原件逻辑行号/全列差异及分类集合见`duplicate-grains.json`，源字节与成员SHA见`source.json`。本机空间计入项目18GB份额；无云资源、GPU或新增常驻服务。
