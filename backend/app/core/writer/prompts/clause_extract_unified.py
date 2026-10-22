"""写标约束单次过正文 Prompt。

职责: 把三层约束规则合成一次 LLM 调用的输入/输出契约。
不做: 不发起调用、不解析 JSON、不决定并发与进度。
"""

from app.core.writer.prompts.clause_extract import (
    GENERATION_GUIDE_SYSTEM,
    HARD_CONSTRAINT_SYSTEM,
    STRUCTURE_CONSTRAINT_SYSTEM,
)

UNIFIED_CLAUSE_NODE_KEY = "writing.unified_clause"

UNIFIED_CLAUSE_EXTRACT_SYSTEM = f"""你是资深标书分析师。请对同一段招标原文同时提取三层约束。

## 层间互斥（必须遵守）
- 每条约束只归入一层，只出现在 hard / structure / guide 其中一个数组
- 证书、扫描件、复印件等可编入投标文件的材料 → 仅 structure 的 material_required
- 招标文件附带的格式模板（投标函、承诺书、授权书等）→ 仅 hard 的 fixed_template
- 评分细则与分值 → 仅 guide 的 scoring_criterion
- 投标人行为/流程违规（串通投标、逾期递交、保证金缴纳时间方式、开标现场核验、踏勘答疑、递交时间地点）→ 三层都不提取
- 不得把同一原文改写后同时放入多层

## 硬约束规则
{HARD_CONSTRAINT_SYSTEM}

## 结构约束规则
{STRUCTURE_CONSTRAINT_SYSTEM}

## 生成引导规则
{GENERATION_GUIDE_SYSTEM}
"""

UNIFIED_CLAUSE_EXTRACT_USER = """## 招标文件内容（第 {page_start}-{page_end} 页）
{content}

## 提取要求
从上述内容同时提取三层约束，输出一个 JSON 对象（不是数组），键必须是 hard、structure、guide：

```json
{{
    "hard": [
        {{
            "sub_type": "disqualification",
            "content": "约束的结构化描述（精简但完整）",
            "raw_text": "招标原文对应段落（原样摘录）",
            "source_page": 页码数字,
            "score_weight": null,
            "metadata": {{
                "risk_consequence": "违反后的后果",
                "applies_to": "适用对象",
                "target_section_hint": "建议对应章节，没有则 null",
                "evidence_hint": "证据要点"
            }}
        }}
    ],
    "structure": [],
    "guide": []
}}
```

某层没有条目时该键输出空数组 []。不得输出 JSON 数组作为顶层。仅输出 JSON 对象，不要添加说明文字。
"""


def get_unified_clause_extract_prompt(
    content: str, page_start: int, page_end: int
) -> tuple[str, str]:
    """获取三层合并提取 Prompt（Registry 优先）。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    system = resolve_system_prompt(UNIFIED_CLAUSE_NODE_KEY, UNIFIED_CLAUSE_EXTRACT_SYSTEM)
    user = resolve_user_template(UNIFIED_CLAUSE_NODE_KEY, UNIFIED_CLAUSE_EXTRACT_USER).format(
        content=content,
        page_start=page_start,
        page_end=page_end,
    )
    return system, user
