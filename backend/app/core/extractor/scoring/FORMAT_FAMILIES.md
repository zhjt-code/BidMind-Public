# 评分表版式家族注册表

评分表版式是**开放集合**：新版式会持续进入，回归压力是结构性的而非偶发。
本表的作用不是穷举版式，而是让"护栏增长与分支增长同速"——每个已知家族都能
被指认到代表 fixture 与对应裁决分支，从而使新版式接入变成有章可循的流程。

配套设施：
- 裁决分支台账 `BRANCH_REGISTRY.md`（家族 → 该家族独占的裁决分支）
- T0 全字段基线 `backend/scripts/scoring_snapshot.py`（旧语料零漂移判据）
- 三态准入脚本 `backend/scripts/run_scoring_holdout_admission.py`

## 新版式接入流程（fixture-first）

1. **落 fixture**：新样本先生成页级 JSON 入 `backend/tests/fixtures/scoring/`
 （生成脚本 `backend/scripts/build_scoring_sample_corpus.py`），语料只增不减。
2. **跑当前产物**：`scoring_snapshot.py` 得到现状，不预设期望。
3. **人工裁定真值**：沿用 holdout 三态语义 VERIFIED / PROVISIONAL / FAILED。
 **PROVISIONAL 不得自动采纳**——这条纪律不因流程简化而放宽。
4. **判定必须结构自证**：新家族的识别依据只能是结构位置或数值闭合
 （列结构同质、父子分值闭合等），**禁止靠词表命中后高置信采纳**。
 判定不确定时走默认路径 + REVIEW 标记，不得硬猜路由。
5. **准入门槛**：旧语料 T0 零漂移 + 新家族真值改善 + 负例零误采纳。

## 分支预算纪律

新增任何裁决分支必须同时提交三件套，缺一不得合入：

1. 家族 ID 归属（登记到本表）
2. 至少一份最小真实 fixture
3. `branch_id` 注册（登记到 `BRANCH_REGISTRY.md`）

理由：历史上分支持续增长而护栏未同步增长，是复发链（同一根因反复出现）的
结构性成因。三件套让每条新分支自带回归护栏。

## 已知家族

| 家族 ID | 判别特征 | 主要裁决分支 | 关联历史 Bug |
|---|---|---|---|
| `flat-detail` | 标准明细表：表头含评分因素/分值/评分标准 | `PARSE_ROUTE/detail-lexical-roles` | 主干常态 |
| `headerless-continuation` | 跨页续表表头丢失，靠数据列特征识别 | `PARSE_ROUTE/detail-headerless-roles` | C、 |
| `embedded-score` | 分值内嵌于名称或规则文本（含括号内分值） | `PARSE_ROUTE/embedded-or-numeric-column` | |
| `numeric-score-column` | 无可辨表头但存在独立数值分值列 | `PARSE_ROUTE/embedded-or-numeric-column` | |
| `framework-volume-def` | 框架表：只声明卷满分/权重不含明细 | `PARSE_ROUTE/framework-table` | |
| `weight-summary` | 平铺"分值构成"声明（技术50/商务50） | `PARSE_ROUTE/weight-summary-only` | |
| `multi-scheme` | 同文档多套独立百分制方案 | `TOTALS/independent-100-volumes` | →702→739→798 |
| `multi-lot` | 多采购包，各包独立评分 | `TOTALS/unassigned-lot-entries` | |
| `grouped-merged-cell` | 分组用合并单元格，组标记与首个子项同行 | `VOLUME/merged-cell-group-marker` | |
| `group-labeled` | 原文组标签独占一行 | `VOLUME/group-label-row-skip` | |
| `weighted-conversion` | 折算制：卷权重 × 卷内得分 | `TOTALS/by-volume-defs` | |
| `grade-band` | 主行满分 + 档次折扣行 | （归并于 `grade_band_consolidator`） | |
| `watermark-polluted` | 水印字符穿透分值单元格 | `ROW/score-cell-recovery-attempt` | |
| `compliance-only` | 资格性/符合性审查表，无分值 | `PARSE_ROUTE/compliance-table` | |
| `directory-template` | 目录页/投标文件格式表（非评分表） | `PARSE_ROUTE/directory-template-skip` | |

## 待建家族（已知盲区）

| 盲区 | 现状 | 阻塞项 |
|---|---|---|
| 多级表头（样本 02 型） | 列为 xfail | 表头层级建模未完成（ADR 闸门1 记录） |
| 竖排字符交叉混排（样本 17） | 记为上游解析层缺陷 | 属 pdfplumber 解析层，非评分链 |
| 父子复合名 vs 父名口径（样本 30 型） | 待人工裁定 | 粒度口径未统一（ADR） |
