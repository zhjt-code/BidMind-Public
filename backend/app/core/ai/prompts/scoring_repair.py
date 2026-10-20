"""评分项提取修补 Prompt。

职责: 在确定性评分提取校验失败时, 要求 LLM 仅输出可锚定的增量修补建议。
不做: 不要求 LLM 全量重提评分表、不直接采纳 LLM 结果。
"""

SCORING_REPAIR_SYSTEM = """你是评标办法评分表修补专家。你的任务不是重新提取整张表,
而是基于系统已经提取出的评分项和校验诊断, 找出缺失或分值错误的评分项, 输出最小增量补丁。

硬性规则:
1. 只能输出原文中有明确证据的补丁, 必须提供逐字引用 evidence_quote。
2. 不要编造评分项、分值、页码或卷别。
3. 表头、小计、总分说明、评分说明文字不能作为评分项。
4. 表内分组/合并单元格是卷归属的优先证据; 组内评分项名称中的"报价/价格"不得覆盖分组归属。
5. 如果证据不足以修补闭环, 返回空数组 []。
"""

SCORING_REPAIR_USER = """请根据以下材料输出评分项修补补丁。

## 评分表原文
{scoring_content}

## 邻近评分说明文字
{context_text}

## 已提取评分项
{existing_items}

## 校验诊断
{diagnostics}

## 输出 JSON
仅输出 JSON 数组。每个元素字段如下:
[
  {{
    "action": "add_fact",
    "case_id": "诊断case ID",
    "evidence_ids": ["chunk:123"],
    "score_item_name": "评分项名称",
    "description": "评分项/评分标准的完整描述",
    "score_weight": 10,
    "lot": "采购包/标段原文，如采购包1",
    "volume": "technical|commercial|price|qualification|other|null",
    "volume_label": "评分表内卷/分组原文，如商务标；不确定填null",
    "score_rules": ["得分或扣分规则"],
    "deduction_rules": ["扣分或不得分规则"],
    "source_pages": [页码],
    "evidence_quote": "原文中能证明该评分项和分值的逐字引用"
  }}
]

无法可靠修补时返回 []。"""


def get_scoring_repair_prompt(
    *,
    scoring_content: str,
    context_text: str,
    existing_items: str,
    diagnostics: str,
) -> tuple[str, str]:
    """获取评分项修补 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scoring_repair"
    system = resolve_system_prompt(node, SCORING_REPAIR_SYSTEM)
    user = resolve_user_template(node, SCORING_REPAIR_USER).format(
        scoring_content=scoring_content,
        context_text=context_text,
        existing_items=existing_items,
        diagnostics=diagnostics,
    )
    return system, user
