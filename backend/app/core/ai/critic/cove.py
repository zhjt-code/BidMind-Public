"""CoVe (Chain-of-Verification) 复核框架（B5.9）。

核心规则：
1. 问题完全模板化（禁止 LLM 生成问题）
2. 约束解码强制 {answer, evidence_page, quote}
3. quote 用 rapidfuzz 回匹 chunk（ratio < 90 判 invalid）
4. 多数决三分类（YES / NO / 无定论）
5. CoVe 不直接改判，只能推入 needs_review 或维持原判
6. 所有调用走 get_judge_client（提取池别名；空池 skip，不回落审查）
7. 失效时保留原 LLM 判定

修复点（critic-quality-fix）：
- 显式把原文证据注入到问题 prompt（此前模板只拿到关键词，quote 校验与引用内容脱节）
- 无可验证证据时 `outcome=skipped + reason=no_evidence`，不再污染 inconclusive 统计
- `_majority_vote` 输出拆分 `reason`：no_valid_quote / tie_vote / all_abstain_or_error

修复点：
- 思考型模型（gemini-3.5-flash 等）的 max_tokens 含思考 token，旧预算 500
 几乎全被思考耗尽，正文 JSON 开头即截断、三问全部 invalid；
- 预算提升到 COVE_MAX_TOKENS，并对截断型 JSONDecodeError 扩容重试一次
 （×2 封顶 COVE_TOKEN_CEILING，复用 验证过的扩容模式）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from rapidfuzz import fuzz

from app.core.ai.critic.flows import CriticFlow, FLOW_GENERIC
from app.core.ai.input_budget import clip_text_keep_ends
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.config import settings
from app.core.observability.instrument import instrument as _observability_instrument

logger = logging.getLogger(__name__)

try:
    from app.core.observability.metrics import cove_outcome_total
    _METRICS = True
except Exception:
    _METRICS = False


QUOTE_MATCH_THRESHOLD = 90
MAX_EVIDENCE_SNIPPETS = 5
MAX_EVIDENCE_CHARS_PER_SNIPPET = 800
MAX_EVIDENCE_TOTAL_CHARS = 3000

# CoVe 答案本身很短（{answer, evidence_page, quote}），但思考型模型的
# max_tokens 预算包含思考 token，必须为思考留出余量；扩容重试封顶 4096。
COVE_MAX_TOKENS = 2000
COVE_TOKEN_CEILING = 4096
# 输入过长 400 时按 reduction_round 收缩证据上下文的重试轮数。
# CoVe 证据已硬限 MAX_EVIDENCE_TOTAL_CHARS=3000, 超长概率极低, 2 轮足够兜底。
COVE_INPUT_SHRINK_RETRIES = 2


@dataclass(frozen=True)
class CoveAnswer:
    question_id: str
    answer: str  # "yes" | "no" | "abstain" | "invalid"
    evidence_page: int
    quote: str
    valid: bool


@dataclass(frozen=True)
class CoveResult:
    trigger: str
    outcome: str  # "agree" | "disagree" | "inconclusive" | "skipped"
    answers: list[CoveAnswer]
    should_escalate: bool
    # 原因细化：no_evidence / no_valid_quote / tie_vote / all_abstain_or_error /
    # unknown_trigger / client_error / ""（正常 agree/disagree）
    reason: str = ""


def should_use_cove(project_id: int | str | None) -> bool:
    if not settings.COVE_ENABLED:
        return False
    ratio = settings.COVE_RATIO
    if ratio <= 0.0 or project_id is None:
        return False
    if ratio >= 1.0:
        return True
    h = int(hashlib.md5(str(project_id).encode()).hexdigest(), 16) % 100
    return h < int(ratio * 100)


@_observability_instrument(
    name="cove_run_seconds",
    layer="quality",
    pillar="quality",
    direction="lower_better",
    business="all",
    measure="latency",
    description="CoVe 复核单次执行耗时",
)
async def run_cove(
    trigger: str,
    template_vars: dict[str, Any],
    chunks: list[str],
    llm_client: Any,
    flow: CriticFlow | str = FLOW_GENERIC,
    *,
    evidence_snippets: Optional[Sequence[dict]] = None,
) -> CoveResult:
    """Execute CoVe verification questions and return majority-vote result.

    Args:
        trigger: CoVe 触发类型（对应 TEMPLATES_BY_TRIGGER 的 key）
        template_vars: 题目模板变量
        chunks: 用于 quote 回匹校验的原文片段；空列表会直接 skip
        llm_client: 走 judge 池的 LLM 客户端
        flow: 指标 label（investigate / collusion / writer）
        evidence_snippets: 可选结构化证据，形如 ``[{"page": int, "text": str}, ...]``；
            比 ``chunks`` 提供更丰富的上下文，会被渲染进问题 prompt。
    """
    from app.core.ai.critic.prompts.cove_templates import (
        TEMPLATES_BY_TRIGGER,
        ANSWER_RESPONSE_FORMAT,
    )

    questions = TEMPLATES_BY_TRIGGER.get(trigger)
    if not questions:
        if _METRICS:
            cove_outcome_total.labels(
                flow=flow, trigger=trigger, outcome="skipped",
            ).inc()
        return CoveResult(
            trigger=trigger, outcome="skipped", answers=[],
            should_escalate=False, reason="unknown_trigger",
        )

    normalized_chunks = [str(c) for c in (chunks or []) if c and str(c).strip()]
    # 无证据直接 skipped，避免伪 inconclusive 污染指标
    if not normalized_chunks and not evidence_snippets:
        if _METRICS:
            cove_outcome_total.labels(
                flow=flow, trigger=trigger, outcome="skipped",
            ).inc()
        return CoveResult(
            trigger=trigger, outcome="skipped", answers=[],
            should_escalate=False, reason="no_evidence",
        )

    evidence_context = _render_evidence_context(evidence_snippets, normalized_chunks)
    validation_pool = _build_validation_pool(evidence_snippets, normalized_chunks)

    answers: list[CoveAnswer] = []

    for q in questions:
        base_prompt = q.user_prompt_template.format_map(
            _SafeDict(template_vars),
        )
        def _compose_cove_prompt(reduction_round: int, _bp: str = base_prompt) -> str:
            # 输入过长时按 reduction_round 收缩证据(首尾保留), 问题骨架不动
            ctx = evidence_context
            if reduction_round > 0:
                budget = max(400, int(len(evidence_context) * (0.5 ** reduction_round)))
                ctx = clip_text_keep_ends(evidence_context, budget)
            return (
                "【参考证据（仅可引用以下内容中的原文）】\n"
                f"{ctx}\n\n"
                "【待验证问题】\n"
                f"{_bp}\n\n"
                "请仅引用上述证据中的原文，不得凭空编造；"
                "若证据不足以判断，answer 填 abstain。"
            )

        async def _send_cove(reduction_round: int, _q=q) -> Any:
            return await _chat_json_with_truncation_retry(
                llm_client,
                user_prompt=_compose_cove_prompt(reduction_round),
                system_prompt=_q.system_prompt,
                response_format=ANSWER_RESPONSE_FORMAT,
            )

        try:
            raw = await retry_on_input_too_long_400(
                operation_name=f"CoVe {q.question_id}",
                initial_payload=0,
                send_request=_send_cove,
                reduce_payload_semantically=lambda _c, n: (n, {"reduction_round": n}),
                max_retries=COVE_INPUT_SHRINK_RETRIES,
                describe_payload=lambda rnd: {"reduction_round": rnd},
            )
            parsed = raw if isinstance(raw, dict) else {}
            ans_str = str(parsed.get("answer", "abstain")).lower()
            if ans_str not in ("yes", "no", "abstain"):
                ans_str = "abstain"
            try:
                page = int(parsed.get("evidence_page", 0))
            except (TypeError, ValueError):
                page = 0
            quote = str(parsed.get("quote", ""))

            valid = _validate_quote(quote, validation_pool)
            # abstain 允许 quote 为空；yes/no 必须可回匹
            if ans_str in ("yes", "no") and not valid:
                ans_str = "invalid"

            answers.append(CoveAnswer(
                question_id=q.question_id,
                answer=ans_str,
                evidence_page=page,
                quote=quote,
                valid=valid,
            ))
        except Exception as e:
            logger.warning("CoVe question %s failed: %s", q.question_id, e)
            answers.append(CoveAnswer(
                question_id=q.question_id,
                answer="invalid",
                evidence_page=0,
                quote="",
                valid=False,
            ))

    outcome, escalate, reason = _majority_vote(answers)

    if _METRICS:
        cove_outcome_total.labels(
            flow=flow, trigger=trigger, outcome=outcome,
        ).inc()

    return CoveResult(
        trigger=trigger,
        outcome=outcome,
        answers=answers,
        should_escalate=escalate,
        reason=reason,
    )


def _is_truncation_error(e: json.JSONDecodeError) -> bool:
    """截断特征判定，与 chat_with_validation保持同一口径。"""
    msg = str(e)
    return "Unterminated" in msg or "Expecting" in msg


async def _chat_json_with_truncation_retry(
    llm_client: Any,
    *,
    user_prompt: str,
    system_prompt: str,
    response_format: dict,
) -> Any:
    """带单次截断扩容重试的 chat_json 包装。

 职责: 屏蔽思考型模型"预算被思考耗尽导致正文截断"的单点故障。
 不做: 不处理非截断异常（原样上抛给 run_cove 的 fail-safe），不做多轮重试。

 首次调用用 COVE_MAX_TOKENS；命中截断型 JSONDecodeError 时扩容一次重试
 （×2，封顶 COVE_TOKEN_CEILING）。重试仍失败则上抛，由调用方按
 answer=invalid 兜底。
 """
    try:
        return await llm_client.chat_json(
            user_message=user_prompt,
            system_message=system_prompt,
            temperature=0.0,
            max_tokens=COVE_MAX_TOKENS,
            json_schema=response_format,
        )
    except json.JSONDecodeError as e:
        if not _is_truncation_error(e):
            raise
        retry_tokens = min(COVE_MAX_TOKENS * 2, COVE_TOKEN_CEILING)
        logger.info(
            "CoVe JSON 截断扩容重试: max_tokens %s->%s, err=%s",
            COVE_MAX_TOKENS, retry_tokens, str(e)[:80],
        )
        return await llm_client.chat_json(
            user_message=user_prompt,
            system_message=system_prompt,
            temperature=0.0,
            max_tokens=retry_tokens,
            json_schema=response_format,
        )


def _validate_quote(quote: str, pool: list[str]) -> bool:
    if not quote or not pool:
        return False
    best = max(fuzz.partial_ratio(quote, c) for c in pool)
    return best >= QUOTE_MATCH_THRESHOLD


def _majority_vote(answers: list[CoveAnswer]) -> tuple[str, bool, str]:
    """Return ``(outcome, should_escalate, reason)``.

    reason 取值：
        - ``""``：正常 agree / disagree 时无额外原因
        - ``"tie_vote"``：yes/no 票数持平
        - ``"no_valid_quote"``：至少有一题实际答了 yes/no 但 quote 无法回匹
        - ``"all_abstain_or_error"``：所有题目 abstain 或 invalid 且无人给过 yes/no
    """
    valid = [a for a in answers if a.valid and a.answer in ("yes", "no")]
    if not valid:
        has_attempted = any(a.answer == "invalid" for a in answers)
        return ("inconclusive", False,
                "no_valid_quote" if has_attempted else "all_abstain_or_error")

    yes_count = sum(1 for a in valid if a.answer == "yes")
    no_count = sum(1 for a in valid if a.answer == "no")

    if yes_count > no_count:
        return "agree", False, ""
    if no_count > yes_count:
        return "disagree", True, ""
    return "inconclusive", False, "tie_vote"


def _render_evidence_context(
    evidence_snippets: Optional[Sequence[dict]],
    normalized_chunks: list[str],
) -> str:
    """把结构化证据与回匹 chunks 合并成可读 prompt 段落。"""
    lines: list[str] = []
    total = 0

    def _append(page: Any, text: str) -> bool:
        nonlocal total
        snippet = text.strip()
        if not snippet:
            return True
        snippet = snippet[:MAX_EVIDENCE_CHARS_PER_SNIPPET]
        page_label = f"第{page}页" if page not in (None, "", 0, "N/A") else "未知页"
        block = f"[{page_label}] {snippet}"
        if total + len(block) > MAX_EVIDENCE_TOTAL_CHARS:
            return False
        lines.append(block)
        total += len(block)
        return True

    if evidence_snippets:
        for item in list(evidence_snippets)[:MAX_EVIDENCE_SNIPPETS]:
            if not isinstance(item, dict):
                continue
            if not _append(item.get("page"), str(item.get("text", ""))):
                break

    if not lines:
        for chunk in normalized_chunks[:MAX_EVIDENCE_SNIPPETS]:
            if not _append(None, chunk):
                break

    if not lines:
        return "（无可用证据片段）"
    return "\n---\n".join(lines)


def _build_validation_pool(
    evidence_snippets: Optional[Sequence[dict]],
    normalized_chunks: list[str],
) -> list[str]:
    """拼出用于 quote 回匹的文本池：合并 snippets 的 text 与 chunks。"""
    pool: list[str] = []
    if evidence_snippets:
        for item in evidence_snippets:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    pool.append(text)
    pool.extend(normalized_chunks)
    return pool


class _SafeDict(dict):
    """format_map 时缺失 key 返回占位符而非 KeyError。"""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"
