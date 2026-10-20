""" 审查观测口径失真防御测试

覆盖两大根因:
1. node_key 绑定: 发送点直接写 contextvar current_node_key，覆盖 _get_review_prompts
 连读两节点造成的 contextvar 污染，确保 attempt 归因到真实分支。
 注意: node_key 不经客户端 kwarg 传递（：AIDispatcher/CloudClient 不接受该参数）。
2. schema_invalid 回写: HTTP 成功但业务输出无效时 outcome 精化为 schema_invalid

构造方式:
- 使用 object.__new__ 绕过 ReviewOrchestrator.__init__, 与 test_pair_review_input_guard.py 同模式
- patch app.core.ai.reviewer.get_registry 与 _get_review_prompts 隔离注册表依赖
- patch app.core.ai.reviewer_parts.review_llm_invoke.get_attempt_recorder 捕获回写调用
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional
from unittest.mock import MagicMock, call, patch

from app.core.ai.llm_outcome import LlmOutcome


# ---------------------------------------------------------------------------
# 共享测试基础设施
# ---------------------------------------------------------------------------

# 满足 ExtractedReviewResult 所有必填字段约束的最小合法载荷
_VALID_REVIEW_DICT: dict[str, Any] = {
    "requirement_id": 1,
    "bid_response_id": 2,
    "category": "technical",
    "requirement_summary": "技术方案要求",
    "response_summary": "已提供完整技术方案",
    "is_compliant": True,
    "compliance_level": "fully",
    "score": 90,
    "comment": "投标方案完整覆盖招标技术要求，无缺项。",
    "risk_level": "none",
}

# 缺少多个必填字段——用于触发 schema_invalid 回写
_INVALID_REVIEW_JSON: str = json.dumps(
    {"requirement_id": 1, "category": "technical"},
    ensure_ascii=False,
)


class _FakeResolved:
    """模拟 PromptRegistry.get_prompt() 返回的 ResolvedPrompt 对象。"""

    temperature = 0.0
    max_tokens = 1500
    system_message = "FAKE_SYS"
    user_template = "FAKE_USER"
    system_version_id = None
    user_version_id = None
    stability_class = "deterministic"


class _CapturingClient:
    """记录每次 chat / chat_json 调用的完整 kwargs 与发送时刻的 contextvar 值。

    - responses 中的 dict      → chat_json 返回该 dict
    - responses 中的 str       → chat 返回该字符串
    - responses 中的 Exception → 对应方法抛出该异常

    contextvar_values_at_chat / contextvar_values_at_chat_json:
        在客户端被调用的时刻读取 current_node_key.get(None)，用于断言
        "发送时刻 contextvar 已被 _send_within_budget 正确改绑"。
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.chat_kwargs_list: list[dict[str, Any]] = []
        self.chat_json_kwargs_list: list[dict[str, Any]] = []
        # 发送时刻捕获的 contextvar 值（绑定验证）
        self.contextvar_values_at_chat: list[Optional[str]] = []
        self.contextvar_values_at_chat_json: list[Optional[str]] = []

    def _consume(self) -> Any:
        if self._idx >= len(self._responses):
            return self._responses[-1]
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    async def chat(self, **kwargs: Any) -> str:
        self.chat_kwargs_list.append(dict(kwargs))
        from app.core.ai.prompt_registry import current_node_key
        self.contextvar_values_at_chat.append(current_node_key.get(None))
        resp = self._consume()
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, dict):
            return json.dumps(resp, ensure_ascii=False)
        return resp

    async def chat_json(self, **kwargs: Any) -> dict:
        self.chat_json_kwargs_list.append(dict(kwargs))
        from app.core.ai.prompt_registry import current_node_key
        self.contextvar_values_at_chat_json.append(current_node_key.get(None))
        resp = self._consume()
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, str):
            return json.loads(resp)
        return resp


def _make_orchestrator(
    client: _CapturingClient,
    *,
    max_retries: int = 2,
) -> Any:
    """绕过 ReviewOrchestrator.__init__ 构造最小可用实例。"""
    from app.core.ai.reviewer import ReviewOrchestrator

    orch = object.__new__(ReviewOrchestrator)
    orch.max_retries = max_retries
    orch.llm_client = client
    orch._project_id = None
    # 默认走 chat_json 路径; 各测试按需覆盖
    orch._resolve_cd_schema = lambda: {"type": "json_object"}
    return orch


def _run_call(
    orch: Any,
    *,
    node_key: Optional[str] = None,
    expects_nested_array_output: bool = False,
    recorder_mock: Optional[MagicMock] = None,
) -> Any:
    """公共执行函数，统一处理 patch 上下文。

    patch 目标说明:
    - app.core.ai.reviewer.get_registry: call_llm_review 在函数体内通过
      `_reviewer_module.get_registry()` 读取 PromptRegistry，该符号从
      app.core.ai.reviewer 命名空间解析，与 test_review_output_budget.py 同路径。
    - app.core.ai.reviewer._get_review_prompts: call_llm_review 通过
      `from app.core.ai.reviewer import _get_review_prompts` 在函数体内按名
      解析，patch 模块命名空间即可覆盖。
    - app.core.ai.reviewer_parts.review_llm_invoke.get_attempt_recorder:
      在 review_llm_invoke.py 顶部 `from app.core.ai.attempt_recorder import
      get_attempt_recorder` 绑定到模块命名空间，patch 该模块内的引用。
    """
    reg_patch = patch(
        "app.core.ai.reviewer.get_registry",
        return_value=MagicMock(get_prompt=MagicMock(return_value=_FakeResolved())),
    )
    prompts_patch = patch(
        "app.core.ai.reviewer._get_review_prompts",
        return_value=("FAKE_SYS", "FAKE_USER", "FAKE_NO_RESP"),
    )

    if recorder_mock is not None:
        recorder_patch = patch(
            "app.core.ai.reviewer_parts.review_llm_invoke.get_attempt_recorder",
            return_value=recorder_mock,
        )
        with reg_patch, prompts_patch, recorder_patch:
            return asyncio.run(
                orch._call_llm_review(
                    "test_prompt",
                    node_key=node_key,
                    expects_nested_array_output=expects_nested_array_output,
                )
            )
    else:
        with reg_patch, prompts_patch:
            return asyncio.run(
                orch._call_llm_review(
                    "test_prompt",
                    node_key=node_key,
                    expects_nested_array_output=expects_nested_array_output,
                )
            )


# ---------------------------------------------------------------------------
# 场景 A: node_key 绑定（根因一）
# ---------------------------------------------------------------------------


class TestNodeKeyBinding:
    """node_key 在 _send_within_budget 发送点直接写入 contextvar current_node_key。

 根因: _get_review_prompts 连读两个 Prompt 节点导致 contextvar 停留在
 review_no_response；改为在每次实际发送前显式设置正确分支的 node_key。

 防线: node_key 不经客户端 kwarg 传递。AIDispatcher（project_id 为空时返回）
 与 CloudClient（无通道 fallback 返回）均无该参数，传参会 TypeError，等于把降级
 变成彻底失败。因此 chat/chat_json 的 kwargs 中永远不得出现 node_key。
 """

    @staticmethod
    def _set_sentinel() -> tuple[Any, Any]:
        """在外部 context 设置哨兵值，返回 token 供后续 reset。

        asyncio.run() 会把调用时刻的 context 拷贝一份传入协程，
        哨兵值因此也会出现在协程初始 context 中；_send_within_budget
        调用 current_node_key.set(node_key) 后，协程内的值即改为目标 node_key，
        而外层 context 保持哨兵不变。这使断言能区分"原有值"与"本函数改绑的值"。
        """
        from app.core.ai.prompt_registry import current_node_key
        token = current_node_key.set("sentinel.before")
        return current_node_key, token

    def test_chat_json_path_binds_node_key_contextvar(self) -> None:
        """走 chat_json 路径时，发送时刻 contextvar 必须已被改绑为传入的 node_key。

        断言方式: 哨兵 → asyncio.run() → 发送时刻读 current_node_key → 应等于目标值。
        """
        client = _CapturingClient([dict(_VALID_REVIEW_DICT)])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: {"type": "json_object"}

        nk_var, token = self._set_sentinel()
        try:
            _run_call(orch, node_key="bid_review.review_item")
        finally:
            nk_var.reset(token)

        assert len(client.contextvar_values_at_chat_json) >= 1, (
            "chat_json 应被调用至少一次，但实际未被调用"
        )
        assert client.contextvar_values_at_chat_json[0] == "bid_review.review_item", (
            f"发送时刻 contextvar 应已改绑为 bid_review.review_item，"
            f"实际值: {client.contextvar_values_at_chat_json[0]}"
        )

    def test_chat_path_binds_node_key_contextvar(self) -> None:
        """走 chat 路径（expects_nested_array_output=True）时，contextvar 同样必须改绑。"""
        valid_str = json.dumps(_VALID_REVIEW_DICT, ensure_ascii=False)
        client = _CapturingClient([valid_str])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: {"type": "json_object"}

        nk_var, token = self._set_sentinel()
        try:
            _run_call(
                orch,
                node_key="bid_review.review_item",
                expects_nested_array_output=True,
            )
        finally:
            nk_var.reset(token)

        assert len(client.contextvar_values_at_chat) >= 1, (
            "chat 应被调用至少一次，但实际未被调用"
        )
        assert client.contextvar_values_at_chat[0] == "bid_review.review_item", (
            f"发送时刻 contextvar 应已改绑为 bid_review.review_item，"
            f"实际值: {client.contextvar_values_at_chat[0]}"
        )

    # 回归护栏 ---
    # node_key 不经客户端 kwarg 传递。AIDispatcher（project_id 为空时）与 CloudClient
    # （无通道 fallback 时）都没有该参数，传参会 TypeError 且恰在系统最脆弱的降级时刻失败。
    # 下面三个用例锁住"chat/chat_json 的 kwargs 中永远不含 node_key"这条不变量，
    # 防止未来有人"顺手"改回传参路径。

    def test_absent_node_key_not_in_chat_kwargs_bug865(self) -> None:
        """ 护栏: node_key=None 时，chat kwargs 不含 node_key。

 AIDispatcher 与 CloudClient 不接受该参数，传参会导致 TypeError。
 """
        valid_str = json.dumps(_VALID_REVIEW_DICT, ensure_ascii=False)
        client = _CapturingClient([valid_str])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: None

        _run_call(orch, node_key=None)

        assert len(client.chat_kwargs_list) >= 1, "chat 应被调用，但实际未被调用"
        received = client.chat_kwargs_list[0]
        assert "node_key" not in received, (
            f": chat kwargs 不得含 node_key（AIDispatcher/CloudClient 不接受），"
            f"实际 kwargs 键: {sorted(received.keys())}"
        )

    def test_absent_node_key_not_in_chat_json_kwargs_bug865(self) -> None:
        """ 护栏: node_key=None 时，chat_json kwargs 不含 node_key。"""
        client = _CapturingClient([dict(_VALID_REVIEW_DICT)])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: {"type": "json_object"}

        _run_call(orch, node_key=None)

        assert len(client.chat_json_kwargs_list) >= 1, "chat_json 应被调用，但实际未被调用"
        received = client.chat_json_kwargs_list[0]
        assert "node_key" not in received, (
            f": chat_json kwargs 不得含 node_key（AIDispatcher/CloudClient 不接受），"
            f"实际 kwargs 键: {sorted(received.keys())}"
        )

    def test_provided_node_key_not_in_chat_kwargs_bug865(self) -> None:
        """ 护栏: node_key 有值时，chat kwargs 同样不含 node_key。

 这是 最关键的断言方向——防止有人把绑定方式从 contextvar 改回 kwarg 传参，
 导致 AIDispatcher/CloudClient 路径抛 TypeError 崩溃。
 """
        valid_str = json.dumps(_VALID_REVIEW_DICT, ensure_ascii=False)
        client = _CapturingClient([valid_str])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: None  # 走 chat 路径

        _run_call(orch, node_key="bid_review.review_item")

        assert len(client.chat_kwargs_list) >= 1, "chat 应被调用，但实际未被调用"
        received = client.chat_kwargs_list[0]
        assert "node_key" not in received, (
            f": 即使传入 node_key，chat kwargs 中也不得出现该键，"
            f"应通过 contextvar 绑定而非传参，"
            f"实际 kwargs 键: {sorted(received.keys())}"
        )

    def test_provided_node_key_not_in_chat_json_kwargs_bug865(self) -> None:
        """ 护栏: node_key 有值时，chat_json kwargs 同样不含 node_key。"""
        client = _CapturingClient([dict(_VALID_REVIEW_DICT)])
        orch = _make_orchestrator(client)
        orch._resolve_cd_schema = lambda: {"type": "json_object"}

        _run_call(orch, node_key="bid_review.review_item")

        assert len(client.chat_json_kwargs_list) >= 1, "chat_json 应被调用，但实际未被调用"
        received = client.chat_json_kwargs_list[0]
        assert "node_key" not in received, (
            f": 即使传入 node_key，chat_json kwargs 中也不得出现该键，"
            f"应通过 contextvar 绑定而非传参，"
            f"实际 kwargs 键: {sorted(received.keys())}"
        )


# ---------------------------------------------------------------------------
# 场景 A4: pair.has_response 分支映射——静态源码断言
#
# 选用静态断言的理由: review_single_pair 在到达 _call_llm_review 调用点之前
# 需要完整的证据装配（DB 查询、evidence_bundle 构造、extractor 结果），
# 构造代价远超防御收益。关键映射表达式本身逻辑极为简单且不依赖运行时状态，
# 静态验证足以锁定行为契约并与 根因一形成闭环。
# ---------------------------------------------------------------------------


def test_review_single_pair_branch_mapping_static() -> None:
    """pair.has_response → node_key 分支映射的静态源码契约锁定（根因一）。

 断言 pair_review.review_single_pair 的源码中必须同时存在:
 - has_response=True 映射到 "bid_review.review_item"
 - has_response=False 映射到 "bid_review.review_no_response"
 """
    from app.core.ai.reviewer_parts import pair_review

    source = inspect.getsource(pair_review.review_single_pair)

    assert '"bid_review.review_item" if pair.has_response' in source, (
        "pair_review.review_single_pair 源码缺失 has_response=True → "
        '"bid_review.review_item" 映射， 修复可能被意外回退'
    )
    assert '"bid_review.review_no_response"' in source, (
        "pair_review.review_single_pair 源码缺失 "
        '"bid_review.review_no_response" 字面量，分支映射不完整'
    )


# ---------------------------------------------------------------------------
# 场景 B: schema_invalid 回写（根因二）
# ---------------------------------------------------------------------------


class TestSchemaInvalidWriteback:
    """HTTP 成功但业务输出无效时，attempt outcome 必须精化为 schema_invalid。

    patch 目标: app.core.ai.reviewer_parts.review_llm_invoke.get_attempt_recorder
    理由: review_llm_invoke.py 顶部第 22 行
    `from app.core.ai.attempt_recorder import get_attempt_recorder`
    将名称绑定到该模块的命名空间，必须 patch 该模块内的引用，而非源模块。
    """

    def test_invalid_output_amends_outcome_to_schema_invalid(self) -> None:
        """LLM 返回必填字段缺失的 JSON 时，amend_last_attempt 必须以 SCHEMA_INVALID 调用。"""
        # 两轮都返回无效载荷（max_retries=2，全部失败）
        client = _CapturingClient([_INVALID_REVIEW_JSON, _INVALID_REVIEW_JSON])
        orch = _make_orchestrator(client, max_retries=2)
        orch._resolve_cd_schema = lambda: None  # 走 chat 路径，结果为字符串

        mock_recorder = MagicMock()
        _run_call(orch, recorder_mock=mock_recorder)

        assert mock_recorder.amend_last_attempt.called, (
            "校验失败后 amend_last_attempt 未被调用，outcome 将错误停留在 valid"
        )
        for actual_call in mock_recorder.amend_last_attempt.call_args_list:
            assert actual_call == call(LlmOutcome.SCHEMA_INVALID), (
                f"amend_last_attempt 入参应为 SCHEMA_INVALID，"
                f"实际: {actual_call}"
            )

    def test_valid_output_does_not_amend_outcome(self) -> None:
        """LLM 返回合法输出时，amend_last_attempt 不得被调用。"""
        valid_str = json.dumps(_VALID_REVIEW_DICT, ensure_ascii=False)
        client = _CapturingClient([valid_str])
        orch = _make_orchestrator(client, max_retries=2)
        orch._resolve_cd_schema = lambda: None

        mock_recorder = MagicMock()
        _run_call(orch, recorder_mock=mock_recorder)

        assert not mock_recorder.amend_last_attempt.called, (
            "校验成功时不得调用 amend_last_attempt，会污染 valid outcome"
        )

    def test_amend_call_count_matches_failure_rounds(self) -> None:
        """每轮校验失败都必须回写一次；回写次数与失败轮数严格一致。"""
        client = _CapturingClient(
            [_INVALID_REVIEW_JSON, _INVALID_REVIEW_JSON, _INVALID_REVIEW_JSON]
        )
        orch = _make_orchestrator(client, max_retries=3)
        orch._resolve_cd_schema = lambda: None

        mock_recorder = MagicMock()
        _run_call(orch, recorder_mock=mock_recorder)

        amend_calls = mock_recorder.amend_last_attempt.call_args_list
        assert len(amend_calls) == 3, (
            f"3 轮全部失败应回写 3 次，实际回写 {len(amend_calls)} 次，"
            f"部分失败轮次的 outcome 将错误停留在 valid"
        )
        for idx, c in enumerate(amend_calls):
            assert c == call(LlmOutcome.SCHEMA_INVALID), (
                f"第 {idx + 1} 次回写入参应为 SCHEMA_INVALID，实际: {c}"
            )

    def test_validation_exception_branch_also_amends(self) -> None:
        """ValidationException 抛出路径（except 分支）同样必须回写 schema_invalid。

        构造方式: 令 chat() 直接抛出 ValidationException。
        ValidationException 不属于 APIStatusError，可穿透
        retry_on_input_too_long_400（该函数仅捕获 APIStatusError），
        最终被 call_llm_review 的 `except ValidationException` 分支捕获。
        """
        from app.core.ai.validators import ValidationException

        exc = ValidationException(
            message="测试用校验异常",
            raw_output="{}",
            errors=[{"loc": ("comment",), "msg": "Field required"}],
        )
        # 两轮均抛出，max_retries=2
        client = _CapturingClient([exc, exc])
        orch = _make_orchestrator(client, max_retries=2)
        orch._resolve_cd_schema = lambda: None  # 走 chat 路径

        mock_recorder = MagicMock()
        _run_call(orch, recorder_mock=mock_recorder)

        amend_calls = mock_recorder.amend_last_attempt.call_args_list
        assert len(amend_calls) == 2, (
            f"2 轮 ValidationException 各应回写 1 次，共 2 次，"
            f"实际 {len(amend_calls)} 次"
        )
        for idx, c in enumerate(amend_calls):
            assert c == call(LlmOutcome.SCHEMA_INVALID), (
                f"ValidationException 分支第 {idx + 1} 次回写入参应为 SCHEMA_INVALID，"
                f"实际: {c}"
            )
