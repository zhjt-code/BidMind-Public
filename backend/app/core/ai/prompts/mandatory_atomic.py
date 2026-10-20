"""高风险原子条件召回 Prompt。

载体无关：表行、编号列举、前附表、★散句都按「一条可独立废标的条件」抽取。
"""

from __future__ import annotations

MANDATORY_ATOMIC_SYSTEM = """你是招标文件分析专家。任务是从给定原文中抽出每一条可独立导致投标无效/废标/否决投标的条件。

## 粒度
- 一条输出 = 一条可单独触发无效/废标的条件。
- 表的一行、编号(1)(2)(3)、前附表一格、带★/▲的句子，粒度相同。
- 禁止把多条独立条件合并成「综合资质/综合否决」。

## 必须抽取
- 资格审查/响应性/符合性条款中每一项具体条件
- 「有下列情形之一…投标无效/废标」后的每一条对应义务
- 带★/▲/※且构成实质性要求的具体条款（不要抽取「★表示实质性要求」这种图例句）
- 「未提供X即无效」「报价超过最高限价否决」等后果绑定条件（抽取其义务，而不是把「未X」当要求原文）

## description 保真（强制）
- description 必须照录招标原文中对投标人的义务/条件句，优先「须/应/需/将…」。
- 禁止把「未提交/未将/未提供」等废标失效情形句当作 description。
- 禁止新增原文没有的否定词（例如把「开标前将回函原件带到现场」写成「开标前未将…带到现场」）。
- 原文窗口内只有情形句、没有义务句时，整句照录情形句，不得另写。

## 不要抽取
- 纯评标程序、开标时间地点、串通投标行为认定
- 评分细项与分值（那是评分通道）
- 合同履约阶段条款

## 分类
category 只能是 QUAL/TECH/COMM/PERF/STAFF/FORMAT。
"""

MANDATORY_ATOMIC_USER = """请从以下招标原文抽取全部可独立废标/无效投标的条件。

## 原文
{content}

## 补充信号（必须被某条输出覆盖，若原文确有）
{hints}

## 输出 JSON 数组
[
  {{
    "category": "QUAL|TECH|COMM|PERF|STAFF|FORMAT",
    "description": "一条独立条件的完整描述",
    "source_pages": [页码],
    "keywords": ["关键词"]
  }}
]

无此类条件时输出 []。只输出 JSON。"""


def get_mandatory_atomic_prompt(content: str, hints: str = "（无）") -> tuple[str, str]:
    """Registry 优先，代码常量兜底。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.mandatory_atomic_extract"
    system = resolve_system_prompt(node, MANDATORY_ATOMIC_SYSTEM)
    user = resolve_user_template(node, MANDATORY_ATOMIC_USER).format(
        content=content,
        hints=hints or "（无）",
    )
    return system, user
