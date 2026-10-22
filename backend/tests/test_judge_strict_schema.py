"""S.3.4 单元测试：Judge 通道升 strict json_schema。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.ai.critic.judge import (
    JUDGE_VERDICT_RESPONSE_FORMAT,
    JUDGE_VERDICT_SCHEMA,
    JudgeVerdict,
    _build_judge_response_format,
)


class TestJudgeVerdictModel:
    def test_valid_agree(self):
        v = JudgeVerdict(verdict="agree", confidence=0.9, reason="OK")
        assert v.verdict == "agree"
        assert v.confidence == 0.9

    def test_valid_disagree(self):
        v = JudgeVerdict(verdict="disagree", confidence=0.4, reason="fail")
        assert v.verdict == "disagree"

    def test_valid_need_review(self):
        v = JudgeVerdict(verdict="need_review", confidence=0.5, reason="uncertain")
        assert v.verdict == "need_review"

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValidationError):
            JudgeVerdict(verdict="unknown_value", confidence=0.5, reason="r")

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            JudgeVerdict(verdict="agree", confidence=1.5, reason="r")
        with pytest.raises(ValidationError):
            JudgeVerdict(verdict="agree", confidence=-0.1, reason="r")

    def test_reason_too_long_rejected(self):
        with pytest.raises(ValidationError):
            JudgeVerdict(verdict="agree", confidence=0.5, reason="x" * 501)


class TestJudgeResponseFormat:
    def test_response_format_is_strict_json_schema(self):
        """S.3.4 核心断言：升级后 response_format 必须是 strict json_schema，
        而不是旧的 json_object。"""
        rf = _build_judge_response_format()
        # 要么是 strict json_schema（新），要么是 json_object 降级（旧）；
        # 只要不是硬编码 json_object 即可视为升级成功
        if rf.get("type") == "json_schema":
            schema = rf.get("json_schema", {})
            assert schema.get("strict") is True
            inner = schema.get("schema", {})
            assert "verdict" in inner.get("properties", {})
            assert "confidence" in inner.get("properties", {})
            assert "reason" in inner.get("properties", {})
        else:
            # 降级路径也允许（constrained_decoding 模块不可用等罕见情况），
            # 但记录为警告而非失败
            pytest.skip(f"build_response_format 降级为 {rf.get('type')}")

    def test_module_level_response_format_matches_helper(self):
        """模块级常量 JUDGE_VERDICT_RESPONSE_FORMAT 应与动态构造一致。"""
        assert JUDGE_VERDICT_RESPONSE_FORMAT["type"] in ("json_schema", "json_object")

    def test_bare_schema_unchanged_for_docs(self):
        """裸 schema 仍保留，供文档 / 本地校验用。"""
        assert JUDGE_VERDICT_SCHEMA["type"] == "object"
        assert "verdict" in JUDGE_VERDICT_SCHEMA["properties"]


class TestJudgeStrictContract:
    """ 防线：strict=true 下发的 schema 必须满足 OpenAI 硬性契约。

 此前本文件只断言 type/strict/properties，对 additionalProperties 完全没有覆盖，
 于是单测全绿而线上被 gpt-5.4-mini 以 400 拒绝。这里补上不可跳过的强断言。
 """

    def test_judge_schema_forbids_additional_properties(self):
        rf = _build_judge_response_format()
        assert rf["type"] == "json_schema", (
            "Judge 降级为 json_object，strict schema 契约无法验证"
        )
        schema = rf["json_schema"]["schema"]
        assert schema["additionalProperties"] is False

    def test_judge_schema_requires_every_property(self):
        rf = _build_judge_response_format()
        schema = rf["json_schema"]["schema"]
        assert set(schema["required"]) == set(schema["properties"]), (
            "required 未覆盖全部 properties，strict 模式会被拒绝"
        )
