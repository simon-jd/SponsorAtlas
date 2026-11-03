# Task 4 标签来源预检

2026-10-23，Task4实现前准备。未创建标签、未训练ER、未计算项目P/R。本项目仍需有可追溯人工判断的雇主pair和难例，不能把自动生成的完美分数作为验收。

检索到[US组织匹配基准npmatch](https://nonprofit-open-data-collective.github.io/npmatch/articles/training-dataset.html)，来源方说明包含人工、算法、LLM及网页验证的混合判断。它可供补充研究，不能仅凭“hand-labeled”标题就认定所有记录均满足本项目人工标签要求；还需核验逐条来源、任务定义、雇主领域相关性及数据许可。当前没有将其作为SponsorAtlas验收集，也没有下载或复制这些数据。

[Splink官方评估说明](https://github.com/moj-analytical-services/splink/blob/master/docs/topic_guides/evaluation/edge_overview.md)要求ground truth支撑边级指标，并指出聚类评估也需关注。后续实现应保留漏召回的分母、区分开发样本与holdout、列出困难同名异企及强ID冲突。

可接受有逐条人工审查证据且匹配目标域的公共标签作为来源；不把尚未证明的人工标签缺口写成永久不可完成。任何外部基准都不能默默替代真实LCA/USCIS/board来源上的雇主解析需求。
