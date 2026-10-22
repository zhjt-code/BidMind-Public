# -*- coding: utf-8 -*-
"""评分文档结构读取 Prompt (物理表跨页分组 + 方案/卷归属)。

职责: 渲染物理表摘要块, 要求 LLM 只对输入中已存在的 table_id 输出分组分配
      JSON(logical_table_key/scheme_key/volume)。
不做: 不判读评分明细行、不直接采纳结果(消费方需先做覆盖率/一致性校验)。
"""

SCORING_TABLE_STRUCTURE_SYSTEM = """你只做物理评分表的跨页分组和方案/卷归属。
输出只能引用输入中存在的table_id、context_ref、cell_ref。
同一跨页续表必须使用同一logical_table_key。
属于同一套总分方案的不同卷必须使用同一scheme_key。
资格性/符合性审查表volume=non_scoring。
相邻物理表各自出现独立的"合计"行且分属不同标题(如商务/技术分别独立合计)
时,不得合并为同一logical_table_key;只有续页本身没有出现新的分卷标题、
且前一张物理表末尾没有出现独立合计行时,才可判定为同一逻辑表的跨页续表。
volume取commercial/technical/price/qualification/service/other(计分卷)时
应尽量同时给出volume_anchor_ref,引用输入"分卷/标题上下文"或物理表内已
存在的分卷/小计标题cell_ref,禁止在没有对应标题证据时凭空填写或按条目
内容语义联想;volume为unspecified/non_scoring时volume_anchor_ref可省略。
anchor存在且可解析时,系统会用其实际指向的标题重新解析卷别并覆盖你填写的
volume,你填写的volume只是辅助兼容字段;若anchor缺失或无法解析到任何卷,
系统会把该表volume降级为unspecified,交由候选阶段基于物理表标题自动判定
真实卷归属,不会因此拒收整份文档——因此如果不确定具体属于哪个卷,应直接
填volume=unspecified,不要猜卷。
禁止复制原文,禁止输出分值或quote。

输出严格 JSON(单个对象), 不要输出任何其他文字。"""

SCORING_TABLE_STRUCTURE_USER = """请对以下物理评分表做跨页分组与方案/卷归属判定。

## 物理表摘要
{table_summaries}

## 输出 JSON
{{
  "assignments": [
    {{
      "table_id": "输入中存在的table_id",
      "logical_table_key": "table_group_1",
      "scheme_key": "scheme_1",
      "volume": "technical|commercial|price|qualification|service|other|unspecified|non_scoring",
      "volume_anchor_ref": "volume为计分卷时尽量填写, 输入中存在的context_ref或cell_ref; 不确定或volume为unspecified/non_scoring时可为null"
    }}
  ]
}}

要求:
- 每个输入table_id必须且只能出现一次, 禁止输出输入中不存在的table_id。
- 同一logical_table_key的所有条目, scheme_key与volume与volume_anchor_ref必须完全一致。
- logical_table_key 形如 table_group_1/table_group_2, scheme_key 形如 scheme_1。
- volume_anchor_ref只能引用输入"分卷/标题上下文"或某物理表内已存在的cell_ref, 禁止编造。"""


def get_scoring_table_structure_prompt(
    *,
    table_summaries: str,
) -> tuple[str, str]:
    """获取评分文档结构读取 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scoring_table_structure"
    system = resolve_system_prompt(node, SCORING_TABLE_STRUCTURE_SYSTEM)
    user = resolve_user_template(node, SCORING_TABLE_STRUCTURE_USER).format(
        table_summaries=table_summaries,
    )
    return system, user
