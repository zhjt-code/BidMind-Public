"""标书编写 -- 评分项正文响应专项检查

职责: 对已确认评分项(scoring_criterion)逐条判定正文响应状态，产出三级 consistency issue
      (未响应 high / 占位弱支撑 medium / 非正文响应事实告知 info)。
不做: 不改草稿、不调 LLM、不做覆盖率分母统计(clause_coverage_diagnostics 职责)、不落库。

定位: 评分响应直接决定投标得分。原覆盖检查把评分项与普通约束混为 warning 级"一般提醒"
      默认折叠，直接影响得分的缺口被淹没。本模块按业务重要性分级，并把价格分等非正文响应项
      从警告分离为事实告知(避免永久噪音警告，保警告公信力)。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from app.core.writer.scoring_response_index import build_scoring_response_index

_SCORING_SUB_TYPE = "scoring_criterion"

# 正文占位/生成失败特征(与 template_route/section_composer 的占位文案对齐)
_PLACEHOLDER_MARKERS = (
    "[待补充",
    "内容生成失败",
    "固定模板内容待补充",
    "内容待补充",
)
_FAILURE_MARKERS = ("内容生成失败",)
_PLACEHOLDER_TOKEN_RE = re.compile(r"\[[^\]]*(?:待补充|内容待补充|固定模板内容待补充)[^\]]*\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MIN_REAL_CONTENT_CHARS = 60
_MAX_PLACEHOLDER_RATIO = 0.25
_MATERIAL_EVIDENCE_KEYWORDS = (
    "人员", "职称", "证书", "资质", "业绩", "合同", "发票",
    "设备", "仪器", "照片", "扫描件", "证明材料",
)

# 自动判"非正文响应"的卷: 仅价格分(纯按报价/计分公式, 正文与素材均无法响应)。
# 资格/业绩/证书类靠"素材响应"(上传证明→data_fill 章节), 属正常响应路径, 刻意不列入。
_NON_BODY_VOLUMES = frozenset({"price"})

# D2: 高分值评分项 score_rules 关键要素覆盖保守检查
# 只检查 >= 10 分的素材证据类评分项，降低误报率
_HIGH_SCORE_THRESHOLD = 10.0
_RULE_KEY_PHRASE_RE = re.compile(r'[\u4e00-\u9fff]{4,}')


def _meta(clause: Any) -> dict[str, Any]:
    raw = getattr(clause, "metadata_json", None)
    return raw if isinstance(raw, dict) else {}


def _draft_has_materials(draft: Any) -> bool:
    """草稿是否使用了素材(source_material_ids 非空), 用于区分"缺素材"与"生成失败"。"""
    raw = getattr(draft, "source_material_ids", None)
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, list) and len(data) > 0


def _effective_score(clause: Any) -> float:
    try:
        val = float(getattr(clause, "score_weight", None) or 0)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _score_item_name(clause: Any) -> str:
    meta = _meta(clause)
    name = meta.get("score_item_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    content = str(getattr(clause, "content", "") or "").strip()
    return content[:40] if content else "未命名评分项"


def _fmt_score(score: float) -> str:
    return str(int(score)) if float(score).is_integer() else str(score)


def _is_placeholder(content: str) -> bool:
    text = content or ""
    if any(marker in text for marker in _FAILURE_MARKERS):
        return True
    if not any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return False

    plain = _HTML_TAG_RE.sub("", text)
    placeholder_matches = list(_PLACEHOLDER_TOKEN_RE.finditer(plain))
    if not placeholder_matches:
        return False

    placeholder_chars = sum(len(match.group(0)) for match in placeholder_matches)
    real_text = _PLACEHOLDER_TOKEN_RE.sub("", plain)
    real_chars = len("".join(real_text.split()))
    total_chars = real_chars + placeholder_chars
    if real_chars < _MIN_REAL_CONTENT_CHARS:
        return True
    return placeholder_chars / max(total_chars, 1) >= _MAX_PLACEHOLDER_RATIO


def _has_unfilled_fields(content: str) -> bool:
    """正文是否仍存在局部待补充字段。"""
    text = content or ""
    if not any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return False
    plain = _HTML_TAG_RE.sub("", text)
    return _PLACEHOLDER_TOKEN_RE.search(plain) is not None


def _is_material_evidence_score(clause: Any) -> bool:
    """评分项是否主要依赖人员/资质/业绩/设备等证明素材响应。"""
    meta = _meta(clause)
    text = " ".join(
        str(part or "")
        for part in (
            _score_item_name(clause),
            meta.get("material_kind"),
            meta.get("evidence_hint"),
        )
    )
    return any(keyword in text for keyword in _MATERIAL_EVIDENCE_KEYWORDS)


def _is_non_body(clause: Any) -> bool:
    meta = _meta(clause)
    if meta.get("non_body_response") is True:
        return True
    return str(meta.get("volume") or "") in _NON_BODY_VOLUMES


def _extract_rule_key_phrases(score_rules_text: str) -> list[str]:
    """从 score_rules 文本中提取4字滑动窗口锚点，供正文覆盖保守检查使用。

    每个连续中文词串以4字步长切分，只要正文包含任一锚点即视为覆盖。
    使用4字窗口而非全词匹配，是因为正文往往是原句的改写而非原文复制。
    """
    runs = _RULE_KEY_PHRASE_RE.findall(score_rules_text or "")
    anchors: set[str] = set()
    for run in runs:
        for i in range(len(run) - 4 + 1):
            anchors.add(run[i : i + 4])
    return list(anchors)


def _body_covers_any_rule_phrase(body_text: str, phrases: list[str]) -> bool:
    """正文（已去HTML）是否包含任一规则关键短语（保守: 命中一个即通过）。"""
    clean = _HTML_TAG_RE.sub("", body_text)
    return any(phrase in clean for phrase in phrases)


def build_scoring_response_issues(
    clauses: list[Any], drafts: Iterable[Any]
) -> list[dict]:
    """对已确认评分项逐条判定正文响应状态，产出三级 issue。

    - scoring_unresponded (high): 有分值、非豁免、未被任何草稿来源引用 → 直接失分
      (文案区分素材类/论述类引导)。
    - scoring_weak_support (medium): 已被引用但正文为占位/生成失败 → 按草稿是否已用素材
      (source_material_ids)细分"缺素材(需上传绑定)"与"素材已备但生成失败(需重试)"。
    - scoring_non_body (info): 价格分(自动)或人工标记项 → 事实告知，非警告；资格/业绩类靠
      素材响应，不属此列。
    """
    scoring = [
        c
        for c in clauses
        if getattr(c, "sub_type", "") == _SCORING_SUB_TYPE
        and int(getattr(c, "confirmed", 0) or 0) == 1
    ]
    if not scoring:
        return []

    draft_list = list(drafts)
    covered = build_scoring_response_index(scoring, clauses, draft_list)

    issues: list[dict] = []
    for clause in scoring:
        clause_id = getattr(clause, "id", None)
        name = _score_item_name(clause)
        score = _effective_score(clause)
        score_txt = f"（满分{_fmt_score(score)}分）" if score > 0 else ""

        if _is_non_body(clause):
            volume = str(_meta(clause).get("volume") or "")
            if volume in _NON_BODY_VOLUMES:
                message = (
                    f"评分项'{name}'{score_txt}属价格分，由报价/计分公式计分，不在正文写作范围"
                )
                detail = (
                    "该评分项按报价或评分公式计分，标书正文与素材均无法直接响应，"
                    "请线下确认报价策略是否满足评分要求。"
                )
            else:
                message = (
                    f"评分项'{name}'{score_txt}已人工标记为非正文响应，不计入正文响应检查"
                )
                detail = (
                    "该评分项已被标记为线下/非正文评定，请自行确认其满足评分要求；"
                    "若该项应通过素材响应(如资质/业绩/证书)，请取消标记并在素材管理上传对应素材、"
                    "在绑定阶段关联到相应章节。"
                )
            issues.append({
                "type": "scoring_non_body",
                "severity": "info",
                "node_id": None,
                "message": message,
                "detail": detail,
            })
            continue

        # 无分值说明项(资格性/符合性)不构成"失分"缺口，不纳入 high/medium
        if score <= 0:
            continue

        refs = covered.get(int(clause_id), []) if clause_id is not None else []
        if not refs:
            issues.append({
                "type": "scoring_unresponded",
                "severity": "high",
                "node_id": None,
                "message": f"评分项'{name}'{score_txt}未被任何章节响应，将直接失分",
                "detail": (
                    "该评分项未出现在任何章节草稿的来源约束中。"
                    "若为素材类评分项(业绩/资质/证书等)，请在素材管理上传对应素材并在绑定阶段关联到相应章节；"
                    "若为论述类，请在正文补充针对性响应内容，否则评标时该项无法得分。"
                ),
            })
            continue

        non_placeholder = [
            d for d in refs if not _is_placeholder(str(getattr(d, "content", "") or ""))
        ]
        has_material = any(_draft_has_materials(d) for d in refs)
        if not non_placeholder:
            weak_node = getattr(refs[0], "outline_node_id", None)
            # 结合 source_material_ids 区分"缺素材(需上传绑定)"与"素材已备但生成失败(需重试)"
            if has_material:
                message = (
                    f"评分项'{name}'{score_txt}对应章节已绑定素材但正文生成失败，请重试生成"
                )
                detail = (
                    "该评分项对应章节已使用素材，但正文仍为占位/生成失败，通常是生成中断所致，"
                    "请对该章节重新生成。"
                )
            else:
                message = (
                    f"评分项'{name}'{score_txt}对应章节缺素材，正文为占位，请上传并绑定素材"
                )
                detail = (
                    "该评分项对应章节未使用任何素材(素材未上传或未绑定到该章节)，无法构成有效响应。"
                    "请在素材管理上传相应证明材料，并在绑定阶段关联到该章节后重新生成。"
                )
            issues.append({
                "type": "scoring_weak_support",
                "severity": "medium",
                "node_id": weak_node,
                "message": message,
                "detail": detail,
            })
            continue

        if (
            not has_material
            and _is_material_evidence_score(clause)
            and any(_has_unfilled_fields(str(getattr(d, "content", "") or "")) for d in refs)
        ):
            weak_node = getattr(refs[0], "outline_node_id", None)
            issues.append({
                "type": "scoring_weak_support",
                "severity": "medium",
                "node_id": weak_node,
                "message": (
                    f"评分项'{name}'{score_txt}对应章节缺素材，正文仍有待补充证明字段"
                ),
                "detail": (
                    "该评分项依赖人员、资质、业绩或设备等证明材料响应，但当前章节未使用任何素材，"
                    "且正文仍存在待补充字段。请上传并绑定相应证明材料后重新生成或手动补齐。"
                ),
            })
            continue  # 与 D2 检查互斥，避免同一评分项双报

        # D2: 高分值素材证据类评分项的 score_rules 关键要素覆盖保守检查
        # 触发条件: 非占位真实正文 + 分值高 + 素材证据类 + score_rules 有内容
        #          + 正文完全未覆盖评分规则关键短语
        # 误报控制: 只检查 >= _HIGH_SCORE_THRESHOLD 的素材证据类项；
        #           正文含任一4字规则锚点即视为通过（宁可漏报不制造误报）
        if (
            non_placeholder
            and score >= _HIGH_SCORE_THRESHOLD
            and _is_material_evidence_score(clause)
        ):
            meta_obj = _meta(clause)
            raw_rules = meta_obj.get("score_rules") or ""
            if isinstance(raw_rules, list):
                rules_text = "、".join(str(r) for r in raw_rules if r)
            else:
                rules_text = str(raw_rules)
            rule_phrases = _extract_rule_key_phrases(rules_text)
            if rule_phrases:
                combined_body = " ".join(
                    str(getattr(d, "content", "") or "") for d in non_placeholder
                )
                if not _body_covers_any_rule_phrase(combined_body, rule_phrases):
                    weak_node = getattr(non_placeholder[0], "outline_node_id", None)
                    issues.append({
                        "type": "scoring_weak_support",
                        "severity": "medium",
                        "node_id": weak_node,
                        "message": (
                            f"评分项'{name}'{score_txt}正文未覆盖评分规则关键要素，"
                            "建议补充具体响应内容"
                        ),
                        "detail": (
                            "正文内容与评分规则中的关键词未能有效对应（保守性弱支撑提示）。"
                            "建议逐条对照评分规则补充具体内容，或上传绑定对应素材证明材料。"
                        ),
                    })

    return issues
