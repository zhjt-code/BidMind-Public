""" 防御测试：用户可见 AI 文本泄漏回归 + 泄漏检测器 + 映射/源码契约。

覆盖：
- 用户报告的 5 段真实泄漏样本清洗后无内部术语
- 「预估得分：X分」短语不被破坏
- leak_detector 正常/边界/合法英文路径
- 前后端映射表一致性（后端为单一真源）
- result_builder 落库过闸、pair_review 注入 category_label 源码契约
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.i18n import (
    DISPLAY_TOKEN_MAP,
    detect_internal_leaks,
    sanitize_user_facing_text,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_TRANSFORMS = (
    REPO_ROOT / "frontend" / "src" / "services" / "progressStatusTransforms.ts"
)

# 用户报告的真实泄漏样本（精简保留关键泄漏形态）
LEAK_FIXTURES: list[str] = [
    (
        "投标文件未对该要求进行独立响应。补充信息中跨类别证据"
        "（technical页34-44、194-197；performance页358-359）均涉及施工工艺。"
        "预估得分：0分"
    ),
    (
        "本要求属类别1文本必须响应类，且招标明确为否决项(is_mandatory=true)。"
        "响应领域与履约保证金提交方案基本不相关(相关性评分15)。"
        "预估得分：0分 | 相关性降分: 响应领域与要求不匹配(相关度15/100)"
    ),
    (
        "项目类型基本不相关（relevance_score<30），实质未响应否决项，"
        "构成实质性不符合，存在废标风险。"
    ),
    (
        "跨类别证据（performance页358,359；staffing页327,173,176；"
        "format页2,4；qualification页348,386,387及324）均未涉及施工标准化执行。"
    ),
    (
        "其他类别证据（如performance页358-359专利、staffing页327等人员、"
        "qualification页8等资质）均未涉及施工测量放样。"
    ),
    "审查失败[LLM_REVIEW_FAILURE]：模型调用超时或输出不合规，请人工复核",
]

_FORBIDDEN_MARKERS = (
    "technical页",
    "performance页",
    "staffing页",
    "qualification页",
    "format页",
    "is_mandatory",
    "relevance_score",
    "LLM_REVIEW_FAILURE",
)


class TestLeakRegressionFixtures:
    @pytest.mark.parametrize("raw", LEAK_FIXTURES, ids=[f"fx{i}" for i in range(1, len(LEAK_FIXTURES) + 1)])
    def test_sanitize_clears_known_leak_markers(self, raw: str):
        out = sanitize_user_facing_text(raw)
        for marker in _FORBIDDEN_MARKERS:
            assert marker not in out, f"残留泄漏标记 {marker!r} in {out!r}"
        assert detect_internal_leaks(out) == []

    def test_preserves_estimated_score_phrase(self):
        raw = LEAK_FIXTURES[0]
        out = sanitize_user_facing_text(raw)
        assert "预估得分：0分" in out or "预估得分:0分" in out.replace(" ", "")

    def test_category_tokens_become_chinese(self):
        out = sanitize_user_facing_text("technical页34；performance页358")
        assert "技术要求" in out
        assert "业绩要求" in out
        assert "technical" not in out.lower()
        assert "performance" not in out.lower()

    def test_is_mandatory_and_relevance_cmp(self):
        out = sanitize_user_facing_text(
            "否决项(is_mandatory=true)，且 relevance_score<30"
        )
        assert "属于否决项" in out
        assert "相关性评分低于30" in out
        assert "is_mandatory" not in out
        assert "relevance_score" not in out


class TestLeakDetector:
    def test_empty_and_pure_chinese(self):
        assert detect_internal_leaks(None) == []
        assert detect_internal_leaks("") == []
        assert detect_internal_leaks("投标文件完全符合招标要求，建议通过。") == []

    def test_detects_field_expr_and_category_page(self):
        hits = detect_internal_leaks(
            "招标明确为否决项(is_mandatory=true)，technical页34，relevance_score<30"
        )
        joined = " ".join(hits).lower()
        assert "is_mandatory" in joined
        assert "relevance_score" in joined
        assert "technical" in joined

    def test_allowlists_iso_and_common_acronyms(self):
        text = "持有ISO9001与CNAS认可证书，详见PDF附件。"
        assert detect_internal_leaks(text) == []

    def test_detects_ledger_jargon_and_requirement_pk(self):
        hits = detect_internal_leaks(
            "项目证据账本已登记「身份证」（第44页，来自要求#43177）"
        )
        joined = " ".join(hits)
        assert "项目证据账本" in joined
        assert "来自要求#" in joined

    def test_detects_license_requirement_pk_second_entity(self):
        hits = detect_internal_leaks("来自要求#88、#99，营业执照已见")
        assert any("来自要求#" in hit for hit in hits)


class TestFrontendBackendTokenMapContract:
    """后端 DISPLAY_TOKEN_MAP 为单一真源；前端 USER_FACING_TOKEN_LABELS 必须覆盖。"""

    def test_frontend_covers_backend_tokens(self):
        assert FRONTEND_TRANSFORMS.is_file(), f"missing {FRONTEND_TRANSFORMS}"
        source = FRONTEND_TRANSFORMS.read_text(encoding="utf-8")
        # 提取 USER_FACING_TOKEN_LABELS 对象字面量中的 key
        block_match = re.search(
            r"const USER_FACING_TOKEN_LABELS[^=]*=\s*\{(.*?)\n\}",
            source,
            flags=re.DOTALL,
        )
        assert block_match, "未找到 USER_FACING_TOKEN_LABELS 定义"
        block = block_match.group(1)
        frontend_keys = set(re.findall(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:", block, re.M))
        missing = sorted(set(DISPLAY_TOKEN_MAP.keys()) - frontend_keys)
        # 后端维度代号 / 行业画像等专属 token 允许前端暂缺，但审查类别与高风险枚举必须齐
        required = {
            "qualification",
            "technical",
            "commercial",
            "performance",
            "staffing",
            "format",
            "fully",
            "partially",
            "inconclusive",
            "unknown",
            "source_clause_ids",
            "source_material_ids",
        }
        missing_required = sorted(required - frontend_keys)
        assert not missing_required, (
            f"前端 USER_FACING_TOKEN_LABELS 缺少必选 token: {missing_required}; "
            f"全部缺失={missing}"
        )


class TestSourceContracts:
    def test_result_builder_sanitizes_on_persist(self):
        path = (
            BACKEND_ROOT
            / "app"
            / "core"
            / "ai"
            / "reviewer_parts"
            / "result_builder.py"
        )
        source = path.read_text(encoding="utf-8")
        assert "sanitize_user_facing_text" in source
        assert "comment=_sx(" in source.replace(" ", "")
        assert "requirement_summary=_sx(" in source.replace(" ", "")
        assert "reconcile_note=_sx(" in source.replace(" ", "")

    def test_pair_review_passes_category_label(self):
        path = (
            BACKEND_ROOT
            / "app"
            / "core"
            / "ai"
            / "reviewer_parts"
            / "pair_review.py"
        )
        source = path.read_text(encoding="utf-8")
        assert "category_label" in source
        assert "translate_token" in source
        # 展示行不得再把英文 category 直接塞进仅有的 category= 槽（须同时有 category_label）
        assert source.count("category_label=") >= 2

    def test_review_prompt_has_category_label_slot(self):
        path = BACKEND_ROOT / "app" / "core" / "ai" / "prompts" / "review.py"
        source = path.read_text(encoding="utf-8")
        assert "{category_label}" in source
        assert "用户态语言契约" in source or "纯中文业务表述" in source

    def test_relevance_guard_suffix_is_business_chinese(self):
        path = (
            BACKEND_ROOT
            / "app"
            / "core"
            / "ai"
            / "gates"
            / "review"
            / "relevance_guard.py"
        )
        source = path.read_text(encoding="utf-8")
        assert "相关度" not in source or "相关性评估" in source
        assert "相关性评估" in source
        assert "相关度{relevance}/100" not in source
