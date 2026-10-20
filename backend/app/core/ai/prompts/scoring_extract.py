"""评分项提取 Prompt（P2 评分表聚焦）。

从"评标办法/评分表/评审标准"整表文本中提取结构化评分项（评分项+满分+分档/扣分规则）。
复用 writer scoring_criterion 的字段语义（score_item_name/max_score/score_rules/deduction_rules）。
"""

SCORING_EXTRACT_SYSTEM = """你是评标办法分析专家，擅长从招标文件的评分表/评审标准表中提取结构化评分项。

## 核心任务
1. 逐项提取评分表中的每个**评分项**及其**满分分值**。
2. 保留该项的**分档/得分规则**与**扣分/不得分规则**。
3. 标注该项所属**卷**（技术标/商务标/价格/资格 等）与**分类**。
4. 只提取"对投标文件评分"的项；纯说明性文字（如评分总则、四舍五入规则）不作为评分项。

## 分类(category)取值（必须严格其一）
- QUAL 资质 / TECH 技术 / COMM 商务 / PERF 业绩 / STAFF 人员 / FORMAT 格式

## 卷(volume)取值（尽力判断，不确定填 null）
- technical(技术标) / commercial(商务标) / price(价格) / qualification(资格) / other
- volume_label 填评分表内对应卷/分组的原文标签（如"商务标"、"技术标"、"资信标"）。
- 表内分组/合并单元格是卷归属的优先证据；组内评分项名称中的"报价/价格"不得覆盖分组归属。

## 卷权重与卷满分（加权制关键）
- 若评分表为"分卷 + 权重折算"(如"技术总分100分,按40%折算; 价格总分100分,按60%折算"):
  - 每个评分项的 volume_weight 填其所属卷权重(0-1, 如 40%→0.4), volume_max 填该卷满分(如 100)。
  - "X总分N分,按M%权重折算"这类**卷说明行**照常作为一项输出: score_weight=N, volume_weight=M/100, volume_max=N(系统会据此剥离为卷定义,不计入子项满分)。
- 单一信封 / 无权重折算: volume_weight 与 volume_max 留 null。

## 关键约束
- score_weight 填该评分项的**满分**数值（数字，不带"分"字）。
- 表格可能跨页，请把同一评分项的所有分档规则合并到该项。
- 不要编造分值；分值不明确的项 score_weight 填 0 并在 description 说明。
- 表头/小计/合计行不作为评分项。
"""

SCORING_EXTRACT_USER = """请从以下评分表内容中提取所有评分项。

## 评分表内容（可能跨页拼接）
{content}

## 输出要求
以 JSON 数组输出，每个评分项包含字段：

```json
[
    {{
        "category": "QUAL|TECH|COMM|PERF|STAFF|FORMAT",
        "score_item_name": "评分项名称",
        "description": "评分项/评分标准的完整描述",
        "score_weight": 10,
        "volume": "technical|commercial|price|qualification|other|null",
        "volume_label": "评分表内卷/分组原文，如商务标；不确定填null",
        "volume_weight": 0.4,
        "volume_max": 100,
        "score_rules": ["得满分/分档得分条件"],
        "deduction_rules": ["扣分或不得分条件，没有则空数组"],
        "source_pages": [页码]
    }}
]
```

若内容不含可提取的评分项，返回空数组 []。
仅输出 JSON 数组，不要任何额外文字。"""


def get_scoring_extract_prompt(content: str) -> tuple[str, str]:
    """获取评分项提取 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scoring_extract"
    system = resolve_system_prompt(node, SCORING_EXTRACT_SYSTEM)
    user = resolve_user_template(node, SCORING_EXTRACT_USER).format(content=content)
    return system, user
