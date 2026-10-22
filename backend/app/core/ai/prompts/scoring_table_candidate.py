# -*- coding: utf-8 -*-
"""评分逻辑表候选读取 Prompt (行角色/单元格引用扁平候选)。

职责: 渲染单个逻辑表组的全部物理行, 要求 LLM 只对输入中已存在的
      row_id/cell_ref/score_signal_ref 输出角色与语义字段选择 JSON。
不做: 不判定cell/row存在性(消费方Task4 validator职责)、不直接采纳结果。
"""

SCORING_TABLE_CANDIDATE_SYSTEM = """你只做一个逻辑评分表组内的行角色和单元格引用选择。
只能返回输入中存在的logical_table_key、table_id、row_id、
cell_ref、score_signal_ref、context_ref。
禁止复制原文,禁止直接填写分值,禁止生成quote。
每个计分主行标item;其后分档行绑定同一item_key并标grade_band。
表头、分组、合计、说明和非评分行必须明确标记。

volume归属完全由系统按物理表内的短标题/分组行做确定性扫描自动推导,
不需要你输出、系统也不会读取你可能填写的任何volume相关信息——请不要在
rows的任何一行中包含"volume"或"volume_anchor_ref"这两个键,不确定某行
属于哪个卷不是你需要解决的问题。请把全部注意力集中在role(item/
grade_band/total/non_scoring等)判定、item_key配对、name_cell_ref/
rule_cell_refs/score_signal_ref的准确引用上,这些字段才是唯一决定物化
结果的依据。

score_signal_ref(建议值,系统会在同行信号可确定时自动覆盖):
- role=item/grade_band时仍需填写你认为最合适的score_signal_ref,但该值
  只是建议——若该行物理上只有一个分值信号,或多个分值信号数值完全相同
  (如条目名称括号内"3分"与右侧分值列"0-3.00"表达的是同一个分值上限),
  系统会确定性选中其中最靠右的分值列, 忽略你填写的ref, 因此这种情况下
  填错或引用不存在的ref也不影响最终结果。
- 只有当该行确实存在多个数值不同的分值信号(如同一行同时出现主项分值与
  某个无关备注数字)时, 你的选择才会被采信, 请优先选择该行的标准分值列。
- 请把主要精力放在role/item_key判定与name_cell_ref/rule_cell_refs的
  准确引用上, 分值信号的最终选择由系统兜底确定性判定, 不是模型必须
  百分百选对的关键字段。

blank-name续行判定(role选择的关键区分, 避免独立计分项被误判为档次行):
- 若某行name列为空但拥有独立的score_signal(自身分值), 且该分值是与同组
  主项并列的独立计分指标(而非主项分值区间内的质量档次), 必须标item,
  并将name_cell_ref指向该行非空的规则/内容cell(而非空白的name列本身)。
- 只有当某行是"同一item"内按较好/一般/较差等质量档次划分、分值低于该
  item主项分值上限的档次说明行时, 才标grade_band。

declared_totals只能来自表内明确的"合计/小计/分值构成"等汇总行(该行汇总
的是多个评分项之和,而非单个评分项自身), 禁止把单个评分项自身名称括号内
的分值上限(如"XX(3分)"这类item自身score_signal已表达的上限)当作
declared_total;本表若不存在这类汇总行, declared_totals必须是空数组;
同一volume在declared_totals中最多出现一次。

declared_totals同样不需要你判断volume: 系统会按该逻辑表的实际卷归属
自动覆盖或清空这一字段, 与你的填写完全无关——如果该逻辑表整体只对应
一个卷, 系统会把每条declared_totals的volume强制改写为该卷; 如果该
逻辑表本身混合了多个卷(不同物理行分属不同卷标题), 系统会直接清空全部
declared_totals(文档级的分卷合计声明由更高层单独闭合, 不消费这里的
局部声明)。因此declared_totals中不要包含"volume"键, 只需按上一段规则
准确识别哪些行是汇总行、填写label_cell_ref与score_signal_ref即可。

输出严格 JSON(单个对象), 不要输出任何其他文字。"""

SCORING_TABLE_CANDIDATE_USER = """请对以下逻辑表组内的全部物理行做角色与引用判定。

## 逻辑表证据
{logical_table_evidence}

## 输出 JSON
{{
  "logical_table_key": "输入中的logical_table_key",
  "table_ids": ["输入中出现的全部table_id, 集合需与输入完全一致"],
  "rows": [
    {{
      "row_id": "输入中存在的row_id",
      "role": "header|group|item|grade_band|total|note|non_scoring",
      "item_key": "role=item/grade_band时必填, 同一评分项的分档行需一致",
      "name_cell_ref": "role=item时必填, 输入中存在的cell_ref",
      "rule_cell_refs": ["role=grade_band时必填, 输入中存在的cell_ref"],
      "score_signal_ref": "role=item/grade_band时必填, 输入中存在的score_signal_ref; 建议值, 同行信号唯一或同值时系统会自动覆盖为最右列的信号"
    }}
  ],
  "declared_totals": [
    {{
      "label_cell_ref": "输入中存在的cell_ref",
      "score_signal_ref": "输入中存在的score_signal_ref"
    }}
  ]
}}

要求:
- table_ids 必须与输入逻辑表的全部物理table_id集合完全一致。
- 每个row_id最多出现一次, 禁止编造输入中不存在的任何ID或数值。
- declared_totals只填汇总多个评分项的"合计/小计/分值构成"行, 不填单个
  评分项自身的分值上限; 没有汇总行就返回空数组; 混合多个卷的逻辑表
  一律返回空数组(该表declared_totals不会被采用)。
- rows中的每一行都不要包含"volume"/"volume_anchor_ref"键, declared_totals
  中的每一项都不要包含"volume"键, 系统会自动确定性推导/注入这些字段的
  真实值, 不需要也不会读取你的输出。"""


def get_scoring_table_candidate_prompt(
    *,
    logical_table_evidence: str,
) -> tuple[str, str]:
    """获取评分逻辑表候选读取 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scoring_table_candidate"
    system = resolve_system_prompt(node, SCORING_TABLE_CANDIDATE_SYSTEM)
    user = resolve_user_template(node, SCORING_TABLE_CANDIDATE_USER).format(
        logical_table_evidence=logical_table_evidence,
    )
    return system, user
