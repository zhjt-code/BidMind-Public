"""评分结构判读 Prompt。

职责: 基于评分结构信号快照(表头/合计行/行骨架/标题/声明句), 要求 LLM 判读
      评分方案与卷边界, 输出可锚定的 SchemeMap JSON。
不做: 不要求 LLM 提取评分明细、不直接采纳 LLM 结果(锚点核验+对账后才生效)。
"""

SCHEME_READER_SYSTEM = """你是评标办法结构判读专家。你的任务不是提取评分明细,
而是根据信号快照判读这份招标文件的评分结构: 有几套独立评分方案、每套方案由哪些卷
(技术/商务/价格等)构成、每张表的哪些行属于哪个方案哪个卷。

关键判读规则:
1. "合计/总分100"行是方案边界的一级证据: 两张表各自合计100分, 通常是两套独立方案
   (如商务评审100分+技术评审100分), 而不是一套200分的方案。
2. 表格前后的章节标题(如"5.2.1 商务评审"/"5.2.2 技术评审")是方案/卷边界的一级证据。
3. 表内的卷头行(如"商务评分（总分32分）"独占一行)表示其后各行切换到该卷,
   直到下一个卷头行为止。
4. "分值构成"声明句(形如"某某标X分、某某标Y分"的原文句子)声明的是同一方案内
   各卷的基准分, 各卷行区间归属必须与声明对账一致。
5. 每个判定必须给出 anchor(逐字引用信号快照中的原文), 不能凭空判断。
6. 信号不足以判读时, 降低 confidence 并在 notes 中说明, 不要编造结构。
7. 所有卷名/分值/标题必须来自信号快照原文, 严禁使用本说明中的任何示例文字或数值。

输出严格 JSON(单个对象), 不要输出任何其他文字。"""

SCHEME_READER_USER = """请判读以下评分结构信号快照。

## 信号快照
{signals_block}
{feedback_block}
## 输出 JSON
{{
  "schemes": [
    {{
      "scheme_index": 1,
      "title": "方案标题原文(如 5.2.1 商务评审); 无明确标题填空串",
      "total_score": 100,
      "volumes": [
        {{
          "volume": "technical|commercial|price|qualification|other|unspecified",
          "label": "卷/分组原文名",
          "max_score": 53,
          "anchor": {{"kind": "declaration|heading|table_row", "page": 1, "table_index": null, "row_index": null, "quote": "逐字引用"}}
        }}
      ]
    }}
  ],
  "assignments": [
    {{
      "page": 10,
      "table_index": 0,
      "row_start": 1,
      "row_end": 2,
      "scheme_index": 1,
      "volume": "commercial",
      "volume_label": "商务评审",
      "boundary_anchor": {{"kind": "heading", "page": 10, "table_index": null, "row_index": null, "quote": "5.2.1 商务评审"}}
    }}
  ],
  "confidence": 0.9,
  "notes": "判读依据或不确定性说明"
}}

要求:
- assignments 必须覆盖快照中所有含分值行骨架的评分表; 表头行/合计行不需要归属。
- 资格评审/符合性审查/否决项等非打分表不要输出 assignment, 也不要为其编造方案。
- 每个 assignment 的 scheme_index 必须是 schemes 中已声明的整数, 禁止 null。
- 同一张表内出现卷头行时, 按卷头行切分为多个 assignment。
- row_start/row_end 为快照 rows 中的 row_index 闭区间。
- 每个 anchor 的 quote 必须逐字来自快照(可被精确匹配核验)。"""


def get_scheme_reader_prompt(
    *,
    signals_block: str,
    feedback: str | None = None,
) -> tuple[str, str]:
    """获取评分结构判读 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scheme_reader"
    system = resolve_system_prompt(node, SCHEME_READER_SYSTEM)
    feedback_block = ""
    if feedback:
        feedback_block = f"\n## 上一轮判读的对账差异(必须修正)\n{feedback}\n"
    user = resolve_user_template(node, SCHEME_READER_USER).format(
        signals_block=signals_block,
        feedback_block=feedback_block,
    )
    return system, user
