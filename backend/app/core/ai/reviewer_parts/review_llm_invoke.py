"""审查 LLM 主调用（从 pair_review 剥离，保持行为逐字不变）

职责: 执行单次审查的 LLM 物理调用，含输出校验重试与输入超长语义收缩兜底
不做: 不构造 Prompt、不装配结果对象、不访问 DB、不做算法 skip 判定、不做证据检索

调用契约（剥离前后保持不变）:
- 唯一生产调用方为 `ReviewOrchestrator._call_llm_review` thin forwarder
- 返回 `Optional[ExtractedReviewResult]`，失败一律返回 None 而非抛出
- 吞掉 ValidationException / APIStatusError / Exception 并记日志，异常类型不外泄
- `inspect.getsource(ReviewOrchestrator._call_llm_review)` 的静态字面量契约由
  forwarder 侧承担（test_critic_quality_fix / test_reviewer_pipeline_contract）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger
from openai import APIStatusError

from app.core.ai.attempt_recorder import get_attempt_recorder
from app.core.ai.input_guard import (
    is_input_too_long_error,
    retry_on_input_too_long_400,
)
from app.core.ai.llm_outcome import LlmOutcome
from app.core.ai.llm_retry_resume import LlmRetryResumeState, merge_resume_max_tokens
from app.core.ai.reviewer_parts.result_assembly import assemble_review_result
from app.core.ai.reviewer_parts.review_output_budget import (
    REVIEW_TOKEN_CEILING,
    escalate_review_max_tokens,
    looks_like_truncated_output,
)
from app.core.ai.validators import (
    ValidationException,
    get_review_judgement_validator,
    get_review_result_validator,
)
from app.core.config import settings
from app.schemas.review import ExtractedReviewResult

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.matcher.requirement_matcher import MatchedPair


async def call_llm_review(
    self: "ReviewOrchestrator",
    user_prompt: str,
    *,
    prompt_rebuilder: Optional[Callable[[int], str]] = None,
    operation_label: str = "投标审查",
    expects_nested_array_output: bool = False,
    pair: Optional["MatchedPair"] = None,
    node_key: Optional[str] = None,
    resume_state: Optional[LlmRetryResumeState] = None,
) -> Optional[ExtractedReviewResult]:
    """调用 LLM 执行审查（输出校验重试 + 输入超长语义收缩兜底）。

 使用 OutputValidator 进行结构化校验，支持自动重试（按 self.max_retries 次数）。
 json_schema 灰度开启时走 chat_json 路径以利用 provider 的 JSON 模式。

 ：传入 `pair` 且 REVIEW_OUTPUT_CONTRACT_SLIM_ENABLED 为真时，改用最小
 判断载荷契约，服务端权威字段由 result_assembly 装配；`pair` 缺省（历史调用方
 与单元测试路径）时沿用旧的整体回显契约。

 ：`node_key` 在实际发送点绑定真实分支节点，避免归因停留在最后一次
 读取的 Prompt 节点；外置校验失败时把该次 attempt 的 outcome 修正为
 schema_invalid，使"HTTP 成功但业务输出无效"可被观测。

 Fix-07 / 问题 4：temperature / max_tokens 改由 PromptRegistry 提供，
 兜底使用历史硬编码值（0.0 / 1500），确保 LLM Center 在线配置对 review 主节点生效。
 stability_class=deterministic 时 PromptRegistry 会强制返回 temperature=0.0。

 ：命中输入过长 400 时，用 retry_on_input_too_long_400 按 reduction_round
 递减预算重建 prompt（首次原样发送；仅压缩 knowledge_context 与证据佐证，骨架不动）；
 收缩到下限仍被服务端拒绝则 fail-loud 记录并放弃该项，绝不静默原样重发。
 """
    # 通过 reviewer 主模块命名空间访问 get_registry，
    # 以兼容 `patch("app.core.ai.reviewer.get_registry")` 测试契约。
    import json as _json
    from app.core.ai import reviewer as _reviewer_module
    from app.core.ai.reviewer import _get_review_prompts

    # pair 缺省时无法装配服务端权威字段，只能沿用旧契约
    slim_contract = bool(settings.REVIEW_OUTPUT_CONTRACT_SLIM_ENABLED) and pair is not None
    validator = (
        get_review_judgement_validator() if slim_contract
        else get_review_result_validator()
    )
    # json_object 约束模式对嵌套对象数组生成存在结构性退化（实测同
    # prompt/模型/温度: JSON 模式产出 [{}] 元素+标量 evidence_pages+幻觉键,
    # 纯 chat 完全正确）。要求输出 rule_assessments 的评分项强制走纯 chat,
    # 普通要求保持 json_object 以减少 jsonrepair 触发率。
    cd_schema = None if expects_nested_array_output else self._resolve_cd_schema()

    # Fix-07: 从 PromptRegistry 读取运行时参数（返回 ResolvedPrompt 已应用 stability_class）
    _resolved = _reviewer_module.get_registry().get_prompt("bid_review.review_item")
    _temperature = _resolved.temperature if _resolved.temperature is not None else 0.0
    _max_tokens = _resolved.max_tokens if _resolved.max_tokens else 1500
    _max_tokens = merge_resume_max_tokens(
        _max_tokens, resume_state, ceiling=REVIEW_TOKEN_CEILING,
    )

    retry_hint = ""
    if resume_state is not None and resume_state.has_payload():
        retry_hint = resume_state.prompt_suffix()
        logger.info(
            f"{operation_label} 继承上次问题状态: max_tokens={_max_tokens}, "
            f"hint_chars={len(resume_state.last_retry_hint)}, "
            f"raw_chars={len(resume_state.last_raw_output)}"
        )
    shrink_retries = max(0, int(settings.REVIEW_INPUT_SHRINK_RETRIES))

    def _remember(*, raw_output: str = "", retry_hint_value: str = "") -> None:
        if resume_state is None:
            return
        resume_state.capture(
            retry_hint=retry_hint_value,
            raw_output=raw_output,
            max_tokens=_max_tokens,
        )

    async def _send_within_budget(reduction_round: int) -> str:
        # 首次 reduction_round=0 原样发送(input_guard 原则)；>0 走语义收缩重建。
        if reduction_round <= 0 or prompt_rebuilder is None:
            base_prompt = user_prompt
        else:
            base_prompt = prompt_rebuilder(reduction_round)
        send_prompt = base_prompt + retry_hint if retry_hint else base_prompt
        system_message = _get_review_prompts()[0]
        # _get_review_prompts 连读两个节点会把 contextvar 留在
        # review_no_response，且它在每次发送前都被调用，故必须在此处改绑真实分支。
        # 不使用客户端的 node_key 入参：只有 ProjectBoundDispatcher 接受该参数，
        # AIDispatcher（project_id 为空）与 CloudClient（无通道 fallback）都不接受，
        # 传参会 TypeError。此处直接写 contextvar，与 ProjectBoundDispatcher 内部
        # 行为等价且对所有客户端类型通用。
        if node_key:
            from app.core.ai.prompt_registry import current_node_key
            current_node_key.set(node_key)
        if cd_schema is not None:
            raw_data = await self.llm_client.chat_json(
                user_message=send_prompt,
                system_message=system_message,
                temperature=_temperature,
                max_tokens=_max_tokens,
                json_schema=cd_schema,
            )
            return _json.dumps(raw_data, ensure_ascii=False)
        return await self.llm_client.chat(
            user_message=send_prompt,
            system_message=system_message,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    def _reduce_round(_current: int, next_round: int) -> tuple[int, dict]:
        return next_round, {"reduction_round": next_round}

    for attempt in range(self.max_retries):
        try:
            raw_response = await retry_on_input_too_long_400(
                operation_name=operation_label,
                initial_payload=0,
                send_request=_send_within_budget,
                reduce_payload_semantically=_reduce_round,
                max_retries=shrink_retries,
                describe_payload=lambda rnd: {"reduction_round": rnd},
            )

            # 使用验证器校验输出
            result = validator.validate(raw_response)

            if result.success:
                logger.debug(f"审查结果校验成功 (attempt {attempt + 1})")
                validated = result.unwrap()
                if slim_contract:
                    return assemble_review_result(validated, pair)
                return validated

            # HTTP 成功但业务输出无效。attempt 已被 record_success 记为
            # valid，此处修正口径；validate 为同步调用，与 record 之间无 await。
            get_attempt_recorder().amend_last_attempt(LlmOutcome.SCHEMA_INVALID)
            # 校验失败，追加重试提示(下一轮 _send_within_budget 会读到新 retry_hint)
            retry_hint = result.get_retry_hint()
            # 必填字段缺失是"输出被 max_tokens 截断"的强信号。仅追加提示语
            # 而不抬预算, 等于同模型+同预算+低温的确定性重放, 截断必然复现(
            # 同源)。评分项 prompt 额外要求 comment 200-400 字与 rule_assessments,
            # 输出量约为普通要求的 3~4 倍, 共用节点预算下必然撞顶。
            if (
                result.error is not None
                and looks_like_truncated_output(result.error.errors)
                and _max_tokens < REVIEW_TOKEN_CEILING
            ):
                _escalated = escalate_review_max_tokens(_max_tokens)
                logger.info(
                    f"{operation_label} 输出疑似截断(必填字段缺失), "
                    f"max_tokens {_max_tokens}->{_escalated} 后重试"
                )
                _max_tokens = _escalated
            # 扩容后再回写，外层起步用「下一档已加到的额度」而非刚失败的那一档
            _remember(raw_output=raw_response, retry_hint_value=retry_hint)
            logger.warning(
                f"校验失败 (attempt {attempt + 1}/{self.max_retries}): "
                f"{result.error.message if result.error else '未知错误'}"
            )

        except ValidationException as e:
            get_attempt_recorder().amend_last_attempt(LlmOutcome.SCHEMA_INVALID)
            logger.warning(f"校验异常 (attempt {attempt + 1}): {e}")
            retry_hint = e.get_retry_hint()
            _remember(retry_hint_value=retry_hint)

        except APIStatusError as e:
            # 输入过长 400 经 input_guard 多轮语义收缩仍失败 → fail-loud(非静默)
            if is_input_too_long_error(e):
                logger.error(
                    f"{operation_label}: 输入过长, {shrink_retries} 轮语义收缩后仍被服务端拒绝, "
                    f"放弃该项 (fail-loud, 不静默原样重发): {e}"
                )
                return None
            logger.error(f"{operation_label} API 错误 (attempt {attempt + 1}): {e}")
            _remember()

        except Exception as e:
            logger.error(f"LLM调用失败 (attempt {attempt + 1}): {e}")
            _remember()

    return None
