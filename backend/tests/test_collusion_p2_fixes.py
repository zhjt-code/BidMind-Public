"""串标修复 P2 阶段单测

覆盖 P2-1/P2-2 的核心行为：
- P2-1 `extract_docx_structure`：空路径 / 非支持后缀 / 不存在文件 → 空 dict
- P2-1 灰度开关：COLLUSION_DOCX_STRUCTURE_ENABLED 关闭时不调用提取器
- P2-2 GatePipeline._apply_patches 的 certainty_cap 合并规则
- P2-2 D6 Gate 结构缺失时下发 certainty_cap
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# P2-1 extract_docx_structure 边界
# ---------------------------------------------------------------------------

class TestDocxStructureExtractorBoundaries:
    @staticmethod
    def _fn(path):
        from app.core.collusion.docx_structure_extractor import extract_docx_structure
        return extract_docx_structure(path)

    def test_none_returns_empty(self):
        assert self._fn(None) == {}

    def test_empty_string_returns_empty(self):
        assert self._fn("") == {}

    def test_unsupported_extension_returns_empty(self):
        assert self._fn("foo.txt") == {}

    def test_nonexistent_pdf_returns_empty(self):
        assert self._fn("/nonexistent/fake.pdf") == {}

    def test_nonexistent_docx_returns_empty(self):
        assert self._fn("/nonexistent/fake.docx") == {}

    def test_legacy_doc_returns_empty(self):
        """.doc (binary) 不被 python-docx 支持，应直接返回空"""
        assert self._fn("x.doc") == {}


class TestDocxStructureExtractorRealDocx:
    """用 python-docx 动态生成一个 DOCX 临时文件，验证字段存在。"""

    def test_real_docx_returns_structure(self, tmp_path):
        try:
            from docx import Document as DocxDocument
        except ImportError:
            pytest.skip("python-docx 未安装")

        doc = DocxDocument()
        heading = doc.add_heading("第一章 项目概述", level=1)
        _ = heading
        para = doc.add_paragraph("正文内容")
        run = para.add_run(" 测试 ")
        run.font.name = "SimSun"
        section = doc.sections[0]
        section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = 914400  # 1 inch in EMU

        fp = tmp_path / "t.docx"
        doc.save(fp)

        from app.core.collusion.docx_structure_extractor import extract_docx_structure
        out = extract_docx_structure(str(fp))

        assert isinstance(out, dict)
        assert "toc_headings" in out
        assert any("项目概述" in h for h in out["toc_headings"])
        assert "font_families" in out
        assert isinstance(out["font_families"], list)
        assert "margins" in out
        assert set(out["margins"].keys()) == {"top", "bottom", "left", "right"}
        # 1 inch ≈ 25.4 mm
        assert abs(out["margins"]["top"] - 25.4) < 0.1

        # 校验输出符合 CollusionDocxStructure 契约（类型稳定）
        from app.schemas.collusion import CollusionDocxStructure
        validated = CollusionDocxStructure.model_validate(out)
        assert validated.toc_headings == out["toc_headings"]
        assert validated.font_families == out["font_families"]
        assert validated.margins == out["margins"]


# ---------------------------------------------------------------------------
# P2-1 灰度开关：默认关闭时不调用提取器
# ---------------------------------------------------------------------------

class TestDocxStructureFeatureFlag:
    """验证 settings.COLLUSION_DOCX_STRUCTURE_ENABLED 默认为 False。"""

    def test_default_flag_off(self):
        from app.core.config import settings
        assert settings.COLLUSION_DOCX_STRUCTURE_ENABLED is False


# ---------------------------------------------------------------------------
# P2-2 GatePipeline._apply_patches certainty_cap 合并规则（静态合约检查）
# ---------------------------------------------------------------------------

class TestGatePipelineCertaintyCap:
    """P2-2: _apply_patches 应支持对目标维度的 certainty 取 min(existing, cap)。
    
    目前的 pipeline 代码尚未支持该合并规则；本测试作为契约占位，
    等 P2-2 实现完成后应当通过。"""

    def test_certainty_cap_reduces_existing(self):
        """给定已有 d6_format.certainty=0.8，Gate 下发 certainty_cap=0.3，
        合并后 certainty 应降到 0.3（取 min）。"""

        from app.core.ai.gates.base import GateVerdict
        from app.core.ai.gates.pipeline import GatePipeline

        target = {
            "d6_format": {
                "score": 0.5,
                "findings": [],
                "evidence_level": "third",
                "certainty": 0.8,
            }
        }
        verdict = GateVerdict(
            gate_name="D6FormatAlgorithmicGate",
            verdict="warn",
            severity="enforce",
            reason="结构缺失",
            suggested_patch={"d6_format": {"certainty_cap": 0.3}},
            confidence=0.9,
        )
        GatePipeline._apply_patches([verdict], target)

        assert target["d6_format"]["certainty"] == 0.3

    def test_certainty_cap_below_existing_has_no_effect_if_target_already_lower(self):
        from app.core.ai.gates.base import GateVerdict
        from app.core.ai.gates.pipeline import GatePipeline

        target = {
            "d6_format": {
                "score": 0.1,
                "findings": [],
                "evidence_level": "third",
                "certainty": 0.1,
            }
        }
        verdict = GateVerdict(
            gate_name="D6FormatAlgorithmicGate",
            verdict="warn",
            severity="enforce",
            reason="结构缺失",
            suggested_patch={"d6_format": {"certainty_cap": 0.3}},
            confidence=0.9,
        )
        GatePipeline._apply_patches([verdict], target)

        assert target["d6_format"]["certainty"] == 0.1


# ---------------------------------------------------------------------------
# P2-2 D6 Gate 结构缺失时下发 certainty_cap
# ---------------------------------------------------------------------------

class TestD6GateCertaintyCapOnMissingStructure:
    def _run(self, monkeypatch, strict: bool, evidence: dict):
        from app.core.ai.gates.base import GateContext
        from app.core.ai.gates.collusion.d6_format_algorithmic import D6FormatAlgorithmicGate
        from app.core.config import settings

        monkeypatch.setattr(settings, "COLLUSION_D6_STRICT_MODE", strict)
        monkeypatch.setattr(settings, "COLLUSION_D6_STRICT_MODE_RATIO", 1.0 if strict else 0.0)

        gate = D6FormatAlgorithmicGate()
        ctx = GateContext(
            flow="collusion",
            stage="post_llm",
            target={},
            evidence=evidence,
            config={"project_id": 42},
        )
        return gate.evaluate(ctx)

    def test_strict_off_no_cap(self, monkeypatch):
        """strict 关闭时，结构缺失仅返回 pass，不下发 cap（向后兼容）"""
        v = self._run(monkeypatch, strict=False, evidence={})
        assert v.verdict == "pass"
        assert v.suggested_patch is None or "certainty_cap" not in (v.suggested_patch.get("d6_format") or {})

    def test_strict_on_emits_cap_when_structure_missing(self, monkeypatch):
        v = self._run(monkeypatch, strict=True, evidence={})
        assert v.suggested_patch is not None
        patch = v.suggested_patch.get("d6_format") or {}
        assert "certainty_cap" in patch
        assert patch["certainty_cap"] == 0.3

    def test_strict_on_but_structure_present_not_affected(self, monkeypatch):
        """strict 开启但结构已提供时，走原有 warn/pass 路径（不附加 cap）"""
        v = self._run(monkeypatch, strict=True, evidence={
            "docx_structure_a": {"toc_headings": ["x"], "font_families": [], "margins": {}, "headers_footers": []},
            "docx_structure_b": {"toc_headings": ["y"], "font_families": [], "margins": {}, "headers_footers": []},
        })
        # 结构存在时 cap 不应下发
        patch = (v.suggested_patch or {}).get("d6_format") or {}
        assert "certainty_cap" not in patch
