"""物理形态 / 程序性要求分类器防御测试。

覆盖场景:
- 8 条物理形态用例（3 条仍命中，5 条移除词条后反转为无命中）
- 4 条程序性用例（3 条仍命中，1 条移除词条后反转为无命中）
- 4 条无关领域负向用例
- 5 条否定词压制用例（含 1 条新增物理形态否定用例）
- 5 条 回归用例（移除误命中词条后必须无命中）
- 2 条载体形态召回保护用例（装订/签章类词条仍正确命中）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.extractor.tools.physical_form_classifier import (
    PhysicalFormVerdict,
    classify_requirement,
    substance_keyword_hits,
)

# ---------------------------------------------------------------------------
# 辅助类型别名，提升参数表可读性
# ---------------------------------------------------------------------------

_CASE = tuple[str, bool, bool, str]  # (text, is_physical_form, is_procedural, confidence)


# ---------------------------------------------------------------------------
# 物理形态用例（8 条）
# pf_01/02/04/06/07：依赖已移除词条，反转为无命中
# pf_03/05/08：仍命中现行词典，预期不变
# ---------------------------------------------------------------------------

PHYSICAL_FORM_CASES: list[_CASE] = [
    (
        # 已反转："投标函" 已从词典移除，无任何关键词命中
        "投标函格式要求：须按招标文件附件一的格式填写，不得缺项。",
        False, False, "high",
    ),
    (
        # 已反转："授权委托书" 已移除；"加盖公司公章" 中间夹有"公司"两字，
        # "加盖公章" 不构成连续子串，无关键词命中
        "提供法定代表人授权委托书，须由法定代表人亲笔签署并加盖公司公章。",
        False, False, "high",
    ),
    (
        # 仍命中："加盖公章" 精确出现
        "投标文件须加盖公章，缺失者视为无效投标。",
        True, False, "high",
    ),
    (
        # 已反转："营业执照副本" 已移除；"加盖企业公章" 中间夹有"企业"两字，
        # "加盖公章" 不构成连续子串，无关键词命中
        "营业执照副本复印件须清晰可辨，加盖企业公章。",
        False, False, "high",
    ),
    (
        # 仍命中："投标文件正本" 精确出现
        "投标文件正本一份、副本三份，须装订成册。",
        True, False, "high",
    ),
    (
        # 已反转："投标报价表""开标一览表" 均已移除，无关键词命中
        "投标报价表须与开标一览表中的总价一致，否则以开标一览表为准。",
        False, False, "high",
    ),
    (
        # 已反转："技术偏离表""商务偏离表" 均已移除，无关键词命中
        "技术偏离表和商务偏离表须分别填写，不得合并。",
        False, False, "high",
    ),
    (
        # 仍命中："投标承诺书" 已移除，但 "法人签字" 保留且精确出现
        "投标承诺书须使用招标方提供的模板，并由法人签字确认。",
        True, False, "high",
    ),
]


# ---------------------------------------------------------------------------
# 程序性用例（4 条）
# proc_01：依赖已移除词条，反转为无命中
# proc_02/03/04：仍命中现行词典，预期不变
# ---------------------------------------------------------------------------

PROCEDURAL_CASES: list[_CASE] = [
    (
        # 已反转："投标保证金""投标保证金缴纳" 均已从词典移除，无关键词命中
        "投标保证金缴纳方式：银行转账，收款账户见招标公告。",
        False, False, "high",
    ),
    (
        # "投标文件递交" 命中 procedural_keywords；"投标文件" 单独不在关键词列表
        "投标文件递交截止时间为2026年5月10日上午9时，逾期不予受理。",
        False, True, "high",
    ),
    (
        "开标时间及地点：2026年5月10日上午9时30分，XX市公共资源交易中心三楼开标室。",
        False, True, "high",
    ),
    (
        "现场踏勘安排：拟于2026年4月28日组织投标人统一踏勘，请提前报名。",
        False, True, "high",
    ),
]


# ---------------------------------------------------------------------------
# 无关领域负向用例（4 条，无变化）
# ---------------------------------------------------------------------------

IRRELEVANT_CASES: list[_CASE] = [
    (
        "技术方案应包含施工重难点分析、质量保证措施及安全应急预案。",
        False, False, "high",
    ),
    (
        "项目经理应具有一级注册建造师资格，且具备5年以上同类工程管理经验。",
        False, False, "high",
    ),
    (
        "近三年内完成不少于3项单项合同金额500万元以上的类似工程业绩。",
        False, False, "high",
    ),
    (
        "投标人须提供最近一期审计报告，证明资产负债率不超过70%。",
        False, False, "high",
    ),
]


# ---------------------------------------------------------------------------
# 否定词压制用例（5 条）
# neg_01：程序性关键词被否定 → confidence="low"，预期不变
# neg_02/03/04：依赖已移除词条 → 反转为无命中（否定词亦不触发）
# neg_05：新增，物理形态关键词被否定 → confidence="low"
# ---------------------------------------------------------------------------

NEGATION_CASES: list[_CASE] = [
    (
        # "不需要" 在 "报名" 前 3 字符内 → negation 触发；
        # "购买招标文件" 的前 10 字符窗口内亦含 "不需要" → 同样触发
        "不需要报名，购买招标文件可直接到场领取。",
        False, True, "low",
    ),
    (
        # 已反转："授权委托书" 已从词典移除，无关键词命中，否定词亦不触发
        "免于提供授权委托书，法定代表人直接参与投标的无需另行授权。",
        False, False, "high",
    ),
    (
        # 已反转："投标函" 已从词典移除，无关键词命中，否定词亦不触发
        "非必须提交投标函，投标人可采用自制格式。",
        False, False, "high",
    ),
    (
        # 已反转："投标承诺书" 已从词典移除，无关键词命中，否定词亦不触发
        "无需提交投标承诺书，改为现场签署诚信承诺函。",
        False, False, "high",
    ),
    (
        # 新增：确认物理形态关键词仍受否定词压制
        # "不要求" 在 "加盖公章" 前 3 字符内 → negation 触发，confidence 降为 low
        "不要求加盖公章，改为法定代表人签字即可。",
        True, False, "low",
    ),
]


# ---------------------------------------------------------------------------
# 回归用例（5 条）
# 词典收敛前上述文本曾错误命中并被 Gate 判为"完全符合"跳过审查；
# 词典移除相关词条后，以下文本必须返回无命中结果。
# ---------------------------------------------------------------------------

BUG_852_REGRESSION_CASES: list[_CASE] = [
    (
        # 商务要求文本；含"投标函"但属实质内容，词典移除"投标函"后不再命中
        "投标报价降点率要求：投标降点率≥10%，以投标函及投标函附录中的报价为准。",
        False, False, "high",
    ),
    (
        # 保证金合规引用条款；"投标保证金" 已移除，不命中
        "投标保证金要求:符合第二章投标人须知第3.4款规定",
        False, False, "high",
    ),
    (
        # 违约处理条款；含"投标保证金"但属实质约定，词典移除后不命中
        "中标人无正当理由拒签合同的，招标人取消其中标资格，其投标保证金不予退还。",
        False, False, "high",
    ),
    (
        # 资格文件要求；含"授权委托书"但属实质资格审查，词典移除后不命中
        "提供法定代表人身份证明文件；如适用，提供法定代表人授权委托书。",
        False, False, "high",
    ),
    (
        # "副本"词条已收紧为"投标文件副本"；"营业执照副本" 不再构成子串命中
        "投标人应为在中华人民共和国境内注册的独立法人，提供有效的营业执照副本复印件。",
        False, False, "high",
    ),
]


# ---------------------------------------------------------------------------
# 载体形态召回保护用例（2 条）
# 确认词典收敛后，纯载体形态词仍可正确命中
# ---------------------------------------------------------------------------

PHYSICAL_FORM_PROTECTED_CASES: list[_CASE] = [
    (
        # "装订要求" 精确命中，属纯载体形态约束
        "投标文件装订要求：采用胶装方式，A4纸大小，侧面装订，不可拆卸且不得含有金属材料，应逐页编码。",
        True, False, "high",
    ),
    (
        # "加盖公章" 精确命中；词典层无法区分复合要求中的形态语义与资格语义，
        # 该场景由上层 Gate 的必审判据拦截，分类器层不做过度剪裁
        "投标人应为境内注册独立法人，提供有效的营业执照复印件并加盖公章。",
        True, False, "high",
    ),
]


# ---------------------------------------------------------------------------
# 参数化测试
# ---------------------------------------------------------------------------

def _make_params(cases: list[_CASE], ids_prefix: str) -> list[pytest.param]:
    return [
        pytest.param(text, pf, proc, conf, id=f"{ids_prefix}_{i:02d}")
        for i, (text, pf, proc, conf) in enumerate(cases, start=1)
    ]


ALL_PARAMS = (
    _make_params(PHYSICAL_FORM_CASES, "pf")
    + _make_params(PROCEDURAL_CASES, "proc")
    + _make_params(IRRELEVANT_CASES, "irrel")
    + _make_params(NEGATION_CASES, "neg")
    + _make_params(BUG_852_REGRESSION_CASES, "bug852")
    + _make_params(PHYSICAL_FORM_PROTECTED_CASES, "pf_protect")
)


@pytest.mark.parametrize(
    "text, expected_pf, expected_proc, expected_conf",
    ALL_PARAMS,
)
def test_classify_requirement(
    text: str,
    expected_pf: bool,
    expected_proc: bool,
    expected_conf: str,
) -> None:
    """validate classify_requirement output against known cases."""
    verdict: PhysicalFormVerdict = classify_requirement(text)

    assert verdict.is_physical_form == expected_pf, (
        f"is_physical_form mismatch for: {text!r}\n"
        f"  got {verdict.is_physical_form}, expected {expected_pf}\n"
        f"  matched: {verdict.matched_keywords}"
    )
    assert verdict.is_procedural == expected_proc, (
        f"is_procedural mismatch for: {text!r}\n"
        f"  got {verdict.is_procedural}, expected {expected_proc}\n"
        f"  matched: {verdict.matched_keywords}"
    )
    assert verdict.confidence == expected_conf, (
        f"confidence mismatch for: {text!r}\n"
        f"  got {verdict.confidence!r}, expected {expected_conf!r}\n"
        f"  negations: {verdict.negations}"
    )


# ---------------------------------------------------------------------------
# 附加边界测试
# ---------------------------------------------------------------------------

def test_empty_string_returns_no_match() -> None:
    """空字符串应返回全 False + high 置信度。"""
    verdict = classify_requirement("")
    assert not verdict.is_physical_form
    assert not verdict.is_procedural
    assert verdict.matched_keywords == []
    assert verdict.negations == []
    assert verdict.confidence == "high"


def test_verdict_is_frozen() -> None:
    """PhysicalFormVerdict 为不可变对象，不允许属性赋值。"""
    verdict = classify_requirement("投标函须加盖公章")
    with pytest.raises((AttributeError, TypeError)):
        verdict.is_physical_form = False  # type: ignore[misc]


def test_matched_keywords_is_list() -> None:
    """matched_keywords 必须是 list，且命中时非空。"""
    verdict = classify_requirement("投标函格式须符合要求，加盖公章。")
    assert isinstance(verdict.matched_keywords, list)
    assert len(verdict.matched_keywords) > 0


def test_negation_does_not_suppress_distant_keyword() -> None:
    """否定词与关键词距离超过 10 字符时不应触发否定压制。"""
    # "不需要" 与 "加盖公章" 之间有足够多字符（超过 10 个汉字），不触发否定
    text = "不需要填写与本项目无关的表格，投标文件须加盖公章。"
    verdict = classify_requirement(text)
    # "加盖公章" 应命中，且 "不需要" 距离较远，不应形成否定压制
    assert verdict.is_physical_form, (
        f"期望 is_physical_form=True，实际为 False。"
        f"matched_keywords={verdict.matched_keywords}"
    )
    assert verdict.confidence == "high", (
        f"期望 confidence='high'，实际为 {verdict.confidence!r}。"
        f"negations={verdict.negations}"
    )


def test_domain_terms_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 load_domain_terms 模块级缓存生效，YAML 文件只读取一次。"""
    import app.core.extractor.tools.physical_form_classifier as mod

    call_count = 0
    original_open = Path.open

    def counting_open(self: Path, *args, **kwargs):  # type: ignore[override]
        nonlocal call_count
        if self.name == "domain_terms.yaml":
            call_count += 1
        return original_open(self, *args, **kwargs)

    # 强制重置缓存以便测试第一次加载
    mod._DOMAIN_TERMS = None
    monkeypatch.setattr(Path, "open", counting_open)

    mod.load_domain_terms()
    mod.load_domain_terms()
    mod.load_domain_terms()

    assert call_count == 1, f"Expected YAML to be read once, got {call_count}"


def test_substance_keyword_hits_detects_tech_scheme() -> None:
    hits = substance_keyword_hits("技术方案须加盖公章")
    assert "技术方案" in hits


def test_substance_keyword_hits_empty_on_pure_seal() -> None:
    assert substance_keyword_hits("投标文件须加盖公章") == []
