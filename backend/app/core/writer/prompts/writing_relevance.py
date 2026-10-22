"""写标约束写作消费分级 Prompt。"""

WRITING_RELEVANCE_SYSTEM = """你是标书编写约束分级专家。
请判断每条约束是否应进入标书正文匹配与生成。

判定原则:
- direct: 可在标书正文、表格、证明材料或成品形态中体现或规避。
- reference: 仅为程序安排、联系方式、开标/递交流程、政策背景等, 不影响正文编写。

铁律:
- 评分项必须为 direct。
- 带 ★/▲/☆ 的实质性废标条款必须为 direct。
- 不确定时必须为 direct。
"""

WRITING_RELEVANCE_USER = """## 待分级约束
{clauses_text}

## 输出 JSON 数组
[
  {{"clause_id": 1, "writing_relevance": "direct", "reason": "可在正文或成品形态体现"}}
]

仅输出 JSON 数组。"""


def get_writing_relevance_prompt(clauses_text: str) -> tuple[str, str]:
    """获取写作消费分级 Prompt。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "writing.relevance_classify"
    system = resolve_system_prompt(node, WRITING_RELEVANCE_SYSTEM)
    user = resolve_user_template(node, WRITING_RELEVANCE_USER).format(
        clauses_text=clauses_text,
    )
    return system, user


__all__ = ["get_writing_relevance_prompt"]
