"""评分项审查的规则辅助：Prompt 补充块构造 + 逐规则判定归一化。

职责: 构造评分项审查的 Prompt 补充块（重点评审/预估得分/逐规则判定要求），
      归一化 LLM 输出的逐规则判定并收敛 estimated_score 辅助字段。
不做: 不调用 LLM/DB、不生成审查结论、不修改投标证据。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.ai.gates.review.scoring_object_detect import classify_scoring_standard_kind
from app.schemas.review import RuleAssessmentItem

_VALID_STATUSES = frozenset({"met", "partial", "not", "unknown"})


def normalize_rule_assessments(
    items: Any,
    max_score: Optional[float] = None,
) -> Optional[list[dict]]:
    """归一化 LLM 输出的 rule_assessments 列表。

    - 非法 status 降级为 unknown
    - points clamp 到 [0, max_score]（如 max_score 已知）
    - 跳过无法解析的条目
    - 空输入/全部跳过返回 None

    Args:
        items: LLM 输出的 rule_assessments 原始值（list[dict]/list[RuleAssessmentItem]/None）
        max_score: 评分项满分，用于 clamp points

    Returns:
        归一化后的 list[dict] 或 None（空输入/所有条目解析失败时）
    """
    if not items:
        return None

    result: list[dict] = []
    for raw_item in items:
        if raw_item is None:
            continue
        if hasattr(raw_item, "model_dump"):
            raw: dict = raw_item.model_dump()
        elif isinstance(raw_item, dict):
            raw = dict(raw_item)
        else:
            logger.debug(f"scoring_rubric: 跳过非 dict 条目 {type(raw_item)}")
            continue

        # 非法 status 前置降级（schema validator 会再次处理，这里保障一致性）
        if raw.get("status") not in _VALID_STATUSES:
            raw["status"] = "unknown"

        try:
            normalized = RuleAssessmentItem(**raw)
        except Exception as exc:
            logger.debug(f"scoring_rubric: RuleAssessmentItem 构造失败，降级 unknown: {exc}")
            raw["status"] = "unknown"
            # 清理可能导致二次失败的字段
            raw.setdefault("rule_excerpt", "")
            raw.setdefault("reason", "")
            raw.setdefault("evidence_pages", [])
            try:
                normalized = RuleAssessmentItem(**raw)
            except Exception:
                logger.debug("scoring_rubric: 二次构造仍失败，跳过该条目")
                continue

        d = normalized.model_dump()
        # points clamp
        if max_score is not None and d.get("points") is not None:
            clamped = min(float(d["points"]), float(max_score))
            if clamped != d["points"]:
                logger.debug(
                    f"scoring_rubric: points {d['points']} clamp → {clamped}"
                    f" (max_score={max_score})"
                )
            d["points"] = clamped

        result.append(d)

    return result if result else None


def reconcile_estimated_score(
    estimated_score: Optional[float],
    rule_assessments: Optional[list[dict]],
    max_score: Optional[float] = None,
) -> tuple[Optional[float], Optional[str]]:
    """收敛 estimated_score 与 rule_assessments points 之和。

    策略（保守优先）：
    - 两者一致（|diff| <= 0.5）→ 直接返回 estimated_score，无 note
    - 不一致且存在可量化 points → 取较低分，附 note
    - 无任何可量化 points → 保留 estimated_score（避免空规则把分打成 0）

    Args:
        estimated_score: LLM 给出的 estimated_score
        rule_assessments: 归一化后的逐规则判定列表
        max_score: 评分项满分（暂用于日志，未来可用于 clamp）

    Returns:
        (final_score, note) — note 为 None 表示无冲突
    """
    if not rule_assessments or estimated_score is None:
        return estimated_score, None

    numeric = [
        float(item["points"])
        for item in rule_assessments
        if item.get("points") is not None
    ]
    if not numeric:
        return estimated_score, None

    points_sum = sum(numeric)
    diff = abs(points_sum - float(estimated_score))
    if diff > 0.5:
        lower = min(float(estimated_score), points_sum)
        if max_score is not None:
            try:
                lower = min(lower, float(max_score))
            except (TypeError, ValueError):
                pass
        note = (
            f"rule_assessments points 之和 {points_sum} 与"
            f" estimated_score {estimated_score} 不一致（差值 {diff:.2f}），"
            f"取较低 {lower}。"
        )
        logger.debug(f"scoring_rubric: {note}")
        return lower, note

    return estimated_score, None


def scoring_supplement(pair: Any) -> str:
    """为评分项(scoring_item / is_key_review)生成"重点评审 + 预估得分"补充块。

    职责: 把评分规则注入 Prompt，要求 LLM 在 comment 给出可得分点/失分点与预估得分区间。
    不做: 不改变 compliance_level / is_compliant 判定标准（预估仅供人工参考）。

    （自 pair_review._scoring_supplement 原样迁入——该文件已贴近 800 行硬阈值。）
    """
    req = getattr(pair, "requirement", None)
    if req is None:
        return ""
    kind = getattr(req, "requirement_kind", "normal")
    is_key = getattr(req, "is_key_review", 0)
    if kind != "scoring_item" and not is_key:
        return ""

    meta = getattr(req, "scoring_metadata", None) or {}
    max_score = meta.get("max_score")
    if max_score is None:
        max_score = getattr(req, "score_weight", None)
    volume = meta.get("volume") or "未标注"
    score_rules = meta.get("score_rules") or []
    deduction_rules = meta.get("deduction_rules") or []
    authoritative_desc = meta.get("authoritative_description")
    fidelity_flag = meta.get("fidelity_flag")

    lines = [
        "\n\n## 【评分项·重点评审】",
        f"本条为评标办法中的评分项，满分 {max_score if max_score is not None else '未知'} 分（卷：{volume}）。"
        "即使非否决项也必须重点评审。",
    ]
    # P0 A2: 优先输出评分表权威原文, 让 LLM 看到评分标准出处而非 LLM 改写文本。
    if authoritative_desc and fidelity_flag != "rewritten":
        lines.append(f"评分表原文：{authoritative_desc}")
    if score_rules:
        lines.append("评分/分档规则：" + "；".join(str(r) for r in score_rules))
    if deduction_rules:
        lines.append("扣分/不得分规则：" + "；".join(str(r) for r in deduction_rules))
    vol_key = str(volume).strip().lower()
    desc = getattr(req, "description", None) or ""
    kind = classify_scoring_standard_kind({
        "requirement_text": desc,
        "scoring_metadata": meta,
    })
    if vol_key == "price" or any(k in desc for k in ("价格分", "投标报价", "评标价")):
        lines.append(
            "价格分专项：先按招标要求核验报价形态。"
            "金额（开标一览表/投标总价/人民币）和下浮/降点/折扣率都是报价对象；"
            "招标只要其中一种则有一种即已报价，两者都要时缺一侧也不得写成未查询到报价。"
            "复述评标办法不等于报价。未查询到对应报价形态则 estimated_score=0，意见必须写「未查询到报价」。"
            "已报价但公式依赖其他投标人（最低评标价/评标基准价/基准价/平均价）时不要编造比较分，"
            "estimated_score 填 null，意见写「比较分待评标」，并写明预估不代表实际评标得分。"
            "已报价且公式可在本标内核验（最高限价、缺漏项）时给出预估分并标明预估，"
            "禁止因已接受计算规则给满分。"
        )
    elif kind == "binary":
        lines.append(
            "硬指标专项：本条为满足得X分、否则0的二分条款。"
            "投标响应实质满足（含优于要求）即满足即满分；不得按技术方案存在性压档。"
            "不得因仅有承诺或技术方案章节而扣分。"
        )
    elif kind == "graded" or vol_key == "technical" or "技术方案" in desc:
        lines.append(
            "技术分专项：有技术方案不等于满分。必须对照评分标准（新意、差异性等）"
            "核实质内容后按档给分。仅有章节标题或空泛承诺不得给满分。"
        )
    max_label = max_score if max_score is not None else "满分"
    lines.append(
        f"请在 JSON 输出中必填字段 estimated_score：本评分项的预估得分"
        f"（0~{max_label} 之间的数值，点估计；不符合/未查询到报价填 0；"
        f"价格分比较公式已报价但单标无法计算时填 null，意见写比较分待评标）；"
        f"并在 comment 中说明可得分点与失分点（200-400字），且在结尾用「预估得分：X分」格式重申该数值便于核验（比较分待评标时不要写预估得分数字）。"
        "预估仅供人工参考，不改变 compliance_level / is_compliant 的既有判定标准。"
    )
    rule_block = build_rule_assessments_prompt_block(meta)
    if rule_block:
        lines.append(rule_block)
    return "\n".join(lines)


def expects_rule_assessments(pair: Any) -> bool:
    """该匹配对的审查 prompt 是否会要求输出 rule_assessments 嵌套对象数组。: provider 的 json_object 约束模式对嵌套对象数组生成存在结构性退化
 （实测同 prompt 同模型: JSON 模式产出 `[{}]`/标量 evidence_pages/幻觉键，
 纯 chat 完全正确），命中该形态的调用必须退回纯 chat。判定条件必须与
 scoring_supplement 注入 rule_assessments 块的条件同源，两侧共用
 build_rule_assessments_prompt_block，禁止各自另写判据。
 """
    req = getattr(pair, "requirement", None)
    if req is None:
        return False
    kind = getattr(req, "requirement_kind", "normal")
    is_key = getattr(req, "is_key_review", 0)
    if kind != "scoring_item" and not is_key:
        return False
    meta = getattr(req, "scoring_metadata", None) or {}
    return bool(build_rule_assessments_prompt_block(meta))


def build_rule_assessments_prompt_block(meta: dict) -> str:
    """生成评分项逐规则判定的 Prompt 补充块。

    供 _scoring_supplement（pair_review.py）调用，在评分项审查 Prompt 中
    追加 rule_assessments 输出规范。无评分/扣分规则时返回空字符串。

    Args:
        meta: requirement.scoring_metadata dict

    Returns:
        Prompt 补充块字符串，或 ""（无规则时）
    """
    score_rules = meta.get("score_rules") or []
    deduction_rules = meta.get("deduction_rules") or []
    if not score_rules and not deduction_rules:
        return ""

    lines = [
        "\n## 【逐规则判定 rule_assessments】",
        "请针对上述每条评分/扣分规则，在 JSON 中额外输出 rule_assessments 列表（每条规则对应一项）:",
        (
            '  {"rule_excerpt":"规则原文摘录(30字内)",'
            '"status":"met|partial|not|unknown",'
            '"points":数值或null,'
            '"reason":"判据说明(简洁)",'
            '"evidence_pages":[页码列表]}'
        ),
        "字段说明:",
        "- status: met=满足该规则; partial=部分满足; not=不满足; unknown=无法判定",
        "- points: 本规则对应预估得分（可量化时填数值, 与得分档对应; 不可量化填 null）",
        "- evidence_pages: 支撑该规则判定的证据页码（无证据填 []）",
        "- 非评分项或无法逐条判定时，rule_assessments 可为 null",
    ]
    return "\n".join(lines)
