"""LLM-as-Judge 框架（B5.11 + P1.2 flow 分流）。

复审走提取池（get_judge_client），输出 agree/disagree/need_review。
提取池空时短路返回 skipped + 计 judge_inconclusive_total 指标，禁止回落审查。

P1.2 升级 (单入口 + flow 分流):
- 按 flow (review/collusion/writer/generic) 选择 Prompt 模板;
- Prompt 模板优先从 Registry 取 (judge.<flow>), 代码常量兜底;
- run_judge 新增 evidence 参数, 支持 writer 侧传入正文/绑定素材/硬约束证据;
- writer flow max_tokens 单独放宽, 便于 judge 给出定位性 reason;
-: 全 flow 预算提升, 防思考型模型把预算耗在思考上导致正文截断。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.ai.critic.flows import (
    FLOW_COLLUSION,
    FLOW_GENERIC,
    FLOW_REVIEW,
    FLOW_WRITER,
    CriticFlow,
    resolve_judge_template_flow,
)
from app.core.ai.critic.prompts.judge_templates import get_judge_templates
from app.core.ai.input_budget import clip_text_keep_ends
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.config import settings
from app.core.observability.instrument import instrument as _observability_instrument

logger = logging.getLogger(__name__)

# P1.2: 各 flow 的 max_tokens 预算。writer 的 reason 要指出素材不匹配位置, 预算单独放宽。
# gemini-3.5-flash 等思考型模型的 max_tokens 含思考 token, 旧值 500/800
# 会被内部思考耗尽, 正文 JSON 开头即截断。max_tokens 是上限而非目标值,
# 提高预算后非思考模型成本不变, 思考模型才有空间完成正文输出。
_MAX_TOKENS_BY_FLOW: dict[str, int] = {
    FLOW_WRITER: 2000,
    FLOW_REVIEW: 1500,
    FLOW_COLLUSION: 1500,
}
_DEFAULT_MAX_TOKENS = 1500
# judge evidence(_format_evidence) 无总长上限, writer 大证据场景可能输入超长。
# 首次原样发送; 命中输入过长 400 后按 reduction_round 收缩 evidence_text。
JUDGE_INPUT_SHRINK_RETRIES = 2
JUDGE_EVIDENCE_BASE_CHARS = 20000


def _judge_evidence_budget(reduction_round: int) -> int | None:
    """reduction_round=0 原样(None); >0 时返回 evidence_text 的递减字符上限。"""
    if reduction_round <= 0:
        return None
    base = max(1000, int(JUDGE_EVIDENCE_BASE_CHARS))
    return max(1000, int(base * (0.5 ** reduction_round)))


try:
    from app.core.observability.metrics import judge_inconclusive_total, judge_outcome_total
    _METRICS = True
except Exception:
    _METRICS = False


# S.3.4: Pydantic 驱动的 strict json_schema
# 之前 `{"type":"json_object"}` 不能校验字段，导致 verdict/confidence/reason 都靠
# Python 侧兜底（L121-125）；升级后 provider 支持时由 schema 保证字段存在与类型。
class JudgeVerdict(BaseModel):
    """Judge 复审的结构化输出。"""
    verdict: Literal["agree", "disagree", "need_review"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=500)


# 裸 JSON Schema 仅供文档/本地校验参考，不直接下发
JUDGE_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["agree", "disagree", "need_review"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["verdict", "confidence", "reason"],
}


def _build_judge_response_format() -> dict:
    """S.3.4: 用 build_response_format(JudgeVerdict) 构造 strict json_schema。

    provider 不支持时走现有 UnsupportedResponseFormatError 降级链（cloud_client.py
    L552-566），最终回退到普通 chat + Python 侧兜底，保持对外契约不变。
    import 失败时退回旧 json_object 形态。
    """
    try:
        from app.core.ai.constrained_decoding import build_response_format
        return build_response_format(JudgeVerdict)
    except Exception as e:
        logger.warning("build_response_format(JudgeVerdict) 失败, 退回 json_object: %s", e)
        return {"type": "json_object"}


# OpenAI 兼容的 response_format，strict json_schema 模式
JUDGE_VERDICT_RESPONSE_FORMAT: dict = _build_judge_response_format()


@dataclass(frozen=True)
class JudgeResult:
    verdict: str  # agree | disagree | need_review | skipped
    confidence: float
    reason: str


def should_use_judge(project_id: int | str | None) -> bool:
    if not settings.JUDGE_ENABLED:
        return False
    if project_id is None:
        return False
    return True


def should_sample(
    project_id: int | str | None,
    is_high_risk: bool = False,
    *,
    sample_key: str | int | None = None,
) -> bool:
    """Determine whether this request gets judge review.

    规则: 100% 高风险 + 按 ``JUDGE_SAMPLING_RATIO`` 的条目级抽样。

    critic-quality-fix：
    - 采样粒度由 ``project_id`` 升级为 ``sample_key``，避免出现
      "同一项目内要么全采要么全不采"的项目级偏差，使 10% 抽样
      真正落到单条目。
    - ``sample_key`` 省略时回退到 ``project_id`` 以保证向后兼容。
    """
    if not should_use_judge(project_id):
        return False
    if is_high_risk:
        return True
    ratio = settings.JUDGE_SAMPLING_RATIO
    if ratio <= 0.0:
        return False
    if ratio >= 1.0:
        return True
    key = str(sample_key) if sample_key is not None else str(project_id)
    h = int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000
    return h < int(ratio * 1000)


def _format_evidence(
    evidence: dict[str, Any] | None,
    max_total_chars: int | None = None,
) -> str:
    """将 evidence dict 渲染为可读文本块, 供 writer/review/collusion judge 消费。

 - writer 常见键: bound_materials=[{title,snippet}], hard_constraints=[str]
 - review/collusion 常见键: snippets=[str], reference=[str]
 - 其他键: 按 `key: value` 逐行追加, value 截断到 600 字符。
 - max_total_chars: 输入过长收缩用; 非空时对渲染结果做首尾保留式截断。
 """
    if not evidence:
        return "(无附加证据)"

    lines: list[str] = []
    bound = evidence.get("bound_materials")
    if isinstance(bound, list) and bound:
        lines.append("[绑定素材]")
        for idx, item in enumerate(bound, 1):
            if isinstance(item, dict):
                title = str(item.get("title", ""))[:120]
                snippet = str(item.get("snippet", ""))[:400]
                lines.append(f"{idx}. {title}: {snippet}")
            else:
                lines.append(f"{idx}. {str(item)[:400]}")

    hard = evidence.get("hard_constraints")
    if isinstance(hard, list) and hard:
        lines.append("[硬约束]")
        for idx, item in enumerate(hard, 1):
            lines.append(f"{idx}. {str(item)[:400]}")

    extras = {
        k: v for k, v in evidence.items()
        if k not in {"bound_materials", "hard_constraints"}
    }
    for k, v in extras.items():
        lines.append(f"[{k}] {str(v)[:600]}")

    text = "\n".join(lines) if lines else "(无附加证据)"
    if max_total_chars is not None and max_total_chars > 0 and len(text) > max_total_chars:
        # 输入过长收缩时对渲染后证据做首尾保留式截断, 避免中段语义被腰斩
        text = clip_text_keep_ends(text, max_total_chars)
    return text


def _resolve_judge_prompts(
    flow: CriticFlow | str,
    *,
    original_output: dict[str, Any],
    context_summary: str,
    evidence: dict[str, Any] | None,
    evidence_max_chars: int | None = None,
) -> tuple[str, str]:
    """P1.2: Registry 优先 + 代码默认兜底, 按 flow 解析 judge prompt。"""
    suffix, code_system, code_user = get_judge_templates(flow)
    node_key = f"judge.{suffix}"

    try:
        from app.core.ai.prompt_registry import registry_first
        return registry_first(
            node_key,
            code_system,
            code_user,
            original_output=_safe_json(original_output),
            context_summary=context_summary or "(无上下文摘要)",
            evidence_text=_format_evidence(evidence, max_total_chars=evidence_max_chars),
        )
    except Exception as e:
        logger.debug("registry_first 解析失败 (node=%s): %s, 使用代码默认", node_key, e)
        fmt_kwargs = dict(
            original_output=_safe_json(original_output),
            context_summary=context_summary or "(无上下文摘要)",
            evidence_text=_format_evidence(evidence, max_total_chars=evidence_max_chars),
        )
        try:
            return code_system, code_user.format(**fmt_kwargs)
        except (KeyError, IndexError) as fmt_err:
            logger.warning("judge code-default format 失败 (%s), 回退原文: %s", node_key, fmt_err)
            return code_system, code_user


@_observability_instrument(
    name="judge_run_seconds",
    layer="quality",
    pillar="quality",
    direction="lower_better",
    business="all",
    measure="latency",
    description="LLM-as-Judge 单次复审耗时",
)
async def run_judge(
    original_output: dict[str, Any],
    context_summary: str,
    llm_client: Any,
    flow: CriticFlow | str = FLOW_GENERIC,
    *,
    evidence: dict[str, Any] | None = None,
) -> JudgeResult:
    """Execute judge evaluation using the judge pool client.

    P1.2: 按 flow 分流 prompt (走 Registry + 代码兜底); evidence 可选,
    writer 场景建议传入 bound_materials + hard_constraints 以便 judge
    做事实级比对。
    """

    if llm_client is None:
        if _METRICS:
            judge_inconclusive_total.labels(flow=flow, reason="no_client").inc()
        return JudgeResult(verdict="skipped", confidence=0.0, reason="judge pool not configured")

    template_flow = resolve_judge_template_flow(str(flow or FLOW_GENERIC))
    max_tokens = _MAX_TOKENS_BY_FLOW.get(template_flow, _DEFAULT_MAX_TOKENS)

    def _compose_judge_prompts(reduction_round: int) -> tuple[str, str]:
        # 首次 reduction_round=0 原样(不裁); >0 按递减预算收缩 evidence/summary
        summary = context_summary
        if reduction_round > 0 and summary and len(summary) > 4000:
            summary = clip_text_keep_ends(
                summary, max(1000, int(4000 * (0.5 ** reduction_round))),
            )
        return _resolve_judge_prompts(
            flow,
            original_output=original_output,
            context_summary=summary,
            evidence=evidence,
            evidence_max_chars=_judge_evidence_budget(reduction_round),
        )

    async def _send_judge(reduction_round: int) -> Any:
        sys_p, usr_p = _compose_judge_prompts(reduction_round)
        # P0-D: Judge 是 strict schema 高风险节点, 禁止接受普通 chat 降级产物;
        # 全候选不支持时上抛, 由下方 except 走 skipped fail-safe。
        return await llm_client.chat_json(
            user_message=usr_p,
            system_message=sys_p,
            temperature=0.0,
            max_tokens=max_tokens,
            json_schema=JUDGE_VERDICT_RESPONSE_FORMAT,
            allow_plain_fallback=False,
        )

    try:
        raw = await retry_on_input_too_long_400(
            operation_name=f"Judge[{flow}]",
            initial_payload=0,
            send_request=_send_judge,
            reduce_payload_semantically=lambda _c, n: (n, {"reduction_round": n}),
            max_retries=JUDGE_INPUT_SHRINK_RETRIES,
            describe_payload=lambda rnd: {"reduction_round": rnd},
        )
        parsed = raw if isinstance(raw, dict) else {}
        verdict = str(parsed.get("verdict", "need_review")).lower()
        if verdict not in ("agree", "disagree", "need_review"):
            verdict = "need_review"
        confidence = float(parsed.get("confidence", 0.5))
        reason = str(parsed.get("reason", ""))

        if _METRICS:
            judge_outcome_total.labels(flow=flow, verdict=verdict).inc()

        return JudgeResult(verdict=verdict, confidence=confidence, reason=reason)

    except Exception as e:
        logger.warning("Judge evaluation failed: %s", e)
        if _METRICS:
            judge_inconclusive_total.labels(flow=flow, reason="error").inc()
        return JudgeResult(verdict="skipped", confidence=0.0, reason=f"judge error: {e}")


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:2000]
    except Exception:
        return str(obj)[:2000]
