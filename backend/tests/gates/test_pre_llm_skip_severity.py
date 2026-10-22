"""测试 try_pre_llm_skip 的 severity 判据与 GateRegistry 非法 severity 处理。

覆盖 ：Gate 降级为 advisory 后 try_pre_llm_skip 仍跳过 LLM 审查的缺陷，
以及 GateRegistry._load_global_overrides 对非法值静默丢弃的行为契约。
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.scoring

from app.core.ai.gates.base import GateVerdict
from app.core.ai.gates.registry import GateRegistry
from app.core.ai.reviewer_parts.pair_review import try_pre_llm_skip

# ---------------------------------------------------------------------------
# patch 目标说明：
#   try_pre_llm_skip 函数体内使用
#       from app.core.ai.gates import build_pipeline_for, should_use_algorithm_first
#   因此 patch 目标应为源模块路径，而非 pair_review 的属性。
# ---------------------------------------------------------------------------
_PATCH_ALGO_FIRST = "app.core.ai.gates.should_use_algorithm_first"
_PATCH_BUILD_PIPELINE = "app.core.ai.gates.build_pipeline_for"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _make_pair(
    requirement_id: int = 1001,
    response_id: int = 2001,
    category: str = "TECH",
    is_mandatory: bool = True,
    description: str = "测试招标要求描述",
    requirement_kind: str = "normal",
    is_key_review: int = 0,
    has_response: bool = True,
    response_content: str = "已提供装订说明",
) -> SimpleNamespace:
    """构造满足 try_pre_llm_skip 实际访问属性的最小 pair stub。"""
    requirement = SimpleNamespace(
        description=description,
        requirement_kind=requirement_kind,
        is_key_review=is_key_review,
    )
    response = SimpleNamespace(content=response_content) if has_response else None
    return SimpleNamespace(
        requirement_id=requirement_id,
        response_id=response_id if has_response else None,
        category=category,
        is_mandatory=is_mandatory,
        has_response=has_response,
        requirement=requirement,
        response=response,
    )


def _make_skip_verdict(severity: str, verdict_type: str = "enforce_patch") -> GateVerdict:
    """构造含 _skip_llm_call 的 GateVerdict。"""
    return GateVerdict(
        gate_name="TestPhysicalFormGate",
        verdict=verdict_type,
        severity=severity,
        reason="单元测试占位",
        suggested_patch={
            "_skip_llm_call": True,
            "compliance_level": "fully",
            "score": 95,
            "comment": "物理形态要求，算法自动通过",
            "risk_level": "none",
        },
    )


def _make_mock_pipeline(verdicts: list[GateVerdict]) -> MagicMock:
    """构造返回指定 verdict 列表的假 pipeline。"""
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = verdicts
    return mock_pipeline


# ---------------------------------------------------------------------------
# A1：severity=enforce + enforce_patch + _skip_llm_call=True → 跳过 LLM
# ---------------------------------------------------------------------------


async def test_skip_when_enforce_severity_and_skip_flag() -> None:
    """severity=enforce 且满足全部条件时，应返回非 None 的审查结果（跳过 LLM）。"""
    pair = _make_pair()
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is not None
    assert result.compliance_level == "fully"
    assert result.score == 95
    assert result.requirement_id == 1001
    assert result.bid_response_id == 2001


# ---------------------------------------------------------------------------
# A2：severity=advisory + enforce_patch + _skip_llm_call=True → 必须返回 None
# ---------------------------------------------------------------------------


async def test_no_skip_when_advisory_severity() -> None:
    """ 核心防线：severity=advisory 时即使 verdict=enforce_patch 也禁止跳过 LLM。"""
    pair = _make_pair()
    pipeline = _make_mock_pipeline([_make_skip_verdict("advisory")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


# ---------------------------------------------------------------------------
# A3：severity=reject → 允许 skip（与 enforce 同侧）
# ---------------------------------------------------------------------------


async def test_skip_when_reject_severity() -> None:
    """severity=reject 同样满足条件，应跳过 LLM 并返回非 None。"""
    pair = _make_pair()
    pipeline = _make_mock_pipeline([_make_skip_verdict("reject")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is not None
    assert result.score == 95


# ---------------------------------------------------------------------------
# A4：should_use_algorithm_first=False → 返回 None，severity=enforce 也无效
# ---------------------------------------------------------------------------


async def test_no_skip_when_algorithm_first_disabled() -> None:
    """should_use_algorithm_first 返回 False 时，无论 severity 均不跳过 LLM。"""
    pair = _make_pair()
    # 提供满足 skip 条件的 verdict，但 algo_first 开关关闭
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=False):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


# ---------------------------------------------------------------------------
# A5a：verdict=warn → 返回 None（不满足 enforce_patch 条件）
# ---------------------------------------------------------------------------


async def test_no_skip_when_verdict_is_warn() -> None:
    """verdict=warn 不满足 enforce_patch 条件，应返回 None。"""
    pair = _make_pair()
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce", verdict_type="warn")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


# ---------------------------------------------------------------------------
# A5b：verdict=pass → 返回 None（不满足 enforce_patch 条件）
# ---------------------------------------------------------------------------


async def test_no_skip_when_verdict_is_pass() -> None:
    """verdict=pass 不满足 enforce_patch 条件，应返回 None。"""
    pair = _make_pair()
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce", verdict_type="pass")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


# ---------------------------------------------------------------------------
# B. GateRegistry 非法 severity 处理
# ---------------------------------------------------------------------------


class _StubGate:
    """最小 Gate stub，仅提供 gate_name 与 default_severity 属性。

    GateRegistry.resolve_severity 只访问这两个属性，无需继承 Gate 抽象类。
    """

    gate_name = "stub_gate"
    default_severity: str = "enforce"


def _minimal_ctx() -> SimpleNamespace:
    """返回满足 GateRegistry.resolve_severity 接口的最小 ctx stub。"""
    return SimpleNamespace(config=None)


# ---------------------------------------------------------------------------
# B6：合法值 off → override 生效，resolve_severity 返回 off
# ---------------------------------------------------------------------------


def test_registry_valid_override_off() -> None:
    """GATE_SEVERITY_OVERRIDES 传入合法值 off 时，override 应生效，resolve_severity 返回 off。"""
    overrides = json.dumps({"stub_gate": "off"})
    registry = GateRegistry(global_overrides=overrides)
    gate = _StubGate()
    ctx = _minimal_ctx()

    result = registry.resolve_severity(gate, ctx)

    assert result == "off"


# ---------------------------------------------------------------------------
# B7：非法值 warn → 不写入 override，保持默认 severity，触发 warning 日志
# ---------------------------------------------------------------------------


def test_registry_invalid_severity_warn_falls_back_to_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """非法值 warn 应被忽略：resolve_severity 回落到 gate.default_severity，
    且日志包含该 gate 名与非法值。
    """
    overrides = json.dumps({"stub_gate": "warn"})
    with caplog.at_level(logging.WARNING, logger="app.core.ai.gates.registry"):
        registry = GateRegistry(global_overrides=overrides)

    gate = _StubGate()
    ctx = _minimal_ctx()

    result = registry.resolve_severity(gate, ctx)

    assert result == "enforce", "非法 override 应被忽略，回落到 default_severity=enforce"
    assert "stub_gate" in caplog.text, "warning 日志应包含 gate 名"
    assert "warn" in caplog.text, "warning 日志应包含非法值"


# ---------------------------------------------------------------------------
# B8：非法 JSON 字符串 → 不抛异常，所有 Gate 保持默认 severity
# ---------------------------------------------------------------------------


def test_registry_invalid_json_does_not_raise() -> None:
    """非法 JSON 字符串传入时，GateRegistry 不应抛异常，Gate 保持 default_severity。"""
    registry = GateRegistry(global_overrides="{ not valid json }")
    gate = _StubGate()
    ctx = _minimal_ctx()

    result = registry.resolve_severity(gate, ctx)

    assert result == "enforce", "非法 JSON 下 override 为空，应回落到 default_severity=enforce"


async def test_no_skip_when_scoring_item_even_if_gate_patches() -> None:
    """：评分项即使 Gate 给出 skip 补丁也必须交 LLM。"""
    pair = _make_pair(requirement_kind="scoring_item", is_mandatory=False, category="format")
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


async def test_no_skip_when_key_review_even_if_gate_patches() -> None:
    pair = _make_pair(is_key_review=1, is_mandatory=False, category="format")
    pipeline = _make_mock_pipeline([_make_skip_verdict("enforce")])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is None


async def test_skip_result_uses_patch_noncompliance() -> None:
    """B1：无响应时 skip 结论必须是不符合，且 is_compliant 与意见一致。"""
    pair = _make_pair(is_mandatory=False, category="format", has_response=False)
    verdict = GateVerdict(
        gate_name="TestPhysicalFormGate",
        verdict="enforce_patch",
        severity="enforce",
        reason="单元测试占位",
        suggested_patch={
            "_skip_llm_call": True,
            "compliance_level": "not",
            "score": 0,
            "comment": "本项属于投标文件装订、签章等程序性要求。投标文件中未找到对应响应，初步判定为不符合。",
            "risk_level": "medium",
        },
    )
    pipeline = _make_mock_pipeline([verdict])

    with patch(_PATCH_BUILD_PIPELINE, return_value=pipeline), \
         patch(_PATCH_ALGO_FIRST, return_value=True):
        result = await try_pre_llm_skip(None, project_id=1, pair=pair)

    assert result is not None
    assert result.compliance_level == "not"
    assert result.is_compliant is False
    assert result.score == 0
    assert "不符合" in result.comment
    assert "[算法skip]" not in result.comment
