# 评分裁决分支注册表（branch_id 台账）

配套模块: `scoring_decision_trace.py`（独立采集通道，默认关闭，不写 diagnostics、不参与裁决）。
机械校验: `backend/tests/test_scoring_decision_trace.py` 保证"源码里的 ID"与"本表登记的 ID"完全一致。

## 用途

回归定位时先跑 T0 差分（`backend/scripts/scoring_snapshot_diff.py`）拿到"哪些字段变了"，
再比对 trace 命中集合拿到"哪条裁决分支变了"。两者的交集通常一句话即可指认根因，
替代过去在 1000+ 行解析器里人肉二分的过程。

反向用法：改动某条分支前，先查本表"覆盖样本"列，即可预估爆炸半径。

## 命名规则

`<域>/<裁决语义>`。ID 锚定**语义**而非代码位置——文件拆分/函数搬移时 ID 随语义走，
历史 trace 快照仍可比对。ID 退役必须在本表标注"已退役 + 接替 ID"，禁止静默删除。

## 已登记分支

### PARSE_ROUTE：确定性解析路由（`scoring_table_parser._parse_scoring_table_routed`）

同一张表最终只会命中其中一条，故该组是"这张表被当作哪类表解析"的唯一答案。

| branch_id | 语义 | 命中意味着 |
|---|---|---|
| `PARSE_ROUTE/no-grid-rows` | 网格不足 2 行 | 表未成形，非 confident |
| `PARSE_ROUTE/directory-template-skip` | 判定为目录模板/投标格式表 | 主动跳过，产 0 项且 confident |
| `PARSE_ROUTE/weight-summary-only` | 仅权重汇总表（无明细列） | 只产卷定义不产评分项 |
| `PARSE_ROUTE/detail-lexical-roles` | 词面表头列角色可信 | 主干最常见路径 |
| `PARSE_ROUTE/framework-table` | 框架表（含卷定义行） | 产卷定义 |
| `PARSE_ROUTE/detail-headerless-roles` | 表头丢失、按数据列特征推断角色 | 跨页续表救回路径（C） |
| `PARSE_ROUTE/payload-roles-outbid-selected` | 装配器列角色产出多于嵌入式/数值列候选 | 载荷角色仲裁胜出 |
| `PARSE_ROUTE/embedded-or-numeric-column` | 嵌入式分值或独立数值分值列 | 仲裁路径 |
| `PARSE_ROUTE/payload-roles-last-retry` | 全部既有路径失败后的载荷角色兜底 | 末位重试 |
| `PARSE_ROUTE/score-columns-defer-to-llm` | 结构检出分值列但确定性全失配 | 交 L1 LLM 兜底（防静默零提取） |
| `PARSE_ROUTE/compliance-table` | 落符合性表判定 | 产 0 评分项 |

### VOLUME：卷归属裁决（`_parse_detail_table` 行循环）

| branch_id | 语义 | 命中意味着 |
|---|---|---|
| `VOLUME/title-row-skip` | 纯卷分组标题行，只切卷不产项 | |
| `VOLUME/group-label-row-skip` | 原文组标签行，只切标签不产项 | 组标签轴来源 |
| `VOLUME/merged-cell-group-marker` | 合并单元格卷标记与首个子项同行 | |

### ROW：行级成项/丢弃裁决（`_parse_detail_table` 行循环）

| branch_id | 语义 | 命中意味着 |
|---|---|---|
| `ROW/total-row-skip` | 合计行跳过 | 不计入评分项 |
| `ROW/score-cell-recovery-attempt` | 分值格异常，走行级证据恢复 | 水印污染族 |

### TOTALS：合计对账裁决（`score_totals`）

| branch_id | 语义 | 命中意味着 |
|---|---|---|
| `TOTALS/empty-items` | 零评分项 | 必然 needs_review |
| `TOTALS/by-volume-defs` | 按卷定义口径核计 | 有权威卷基准 |
| `TOTALS/independent-100-volumes` | 多个独立百分制卷口径 | 族 |
| `TOTALS/global-single-scale` | 全局单一百分制口径 | 无卷定义 |
| `TOTALS/structure-signal-forces-review` | 数值通过但结构告警强制复核 | 门禁 |
| `TOTALS/unassigned-lot-entries` | 存在未归包条目 | 多包场景缺口 |

合计 21 条。

## 已退役分支

（暂无）
