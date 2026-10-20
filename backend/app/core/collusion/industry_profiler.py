"""串标审查行业画像生成器 (S1d)

基于招标文件或投标文件信息，调用 LLM 生成行业画像，
标识标准化内容域（相似属正常）和个性化内容域（相似即可疑），
用于指导 D3/D6/D7 维度的文本雷同判定。

生成时机: S1 阶段，与 S1b/S1c 并行执行，1 次 LLM 调用。
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from loguru import logger

from app.core.ai.ai_factory import get_review_client
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.config import get_collusion_extract_timeout
from app.core.ai.prompt_registry import resolve_user_template, resolve_system_prompt
from app.core.collusion.prompts import (
    COLLUSION_INDUSTRY_SIGNAL_PROMPT,
    COLLUSION_INDUSTRY_PROFILE_PROMPT_V2,
)
from app.core.collusion.system_prompts import (
    COLLUSION_INDUSTRY_SIGNAL_SYSTEM_PROMPT,
    COLLUSION_INDUSTRY_PROFILE_SYSTEM_PROMPT,
)
from app.core.collusion.industry_profile_context import (
    IndustryProfileContextInput,
    assess_industry_signal_adequacy,
    build_deterministic_profile_from_signals,
    build_industry_signal_context,
    build_profile_generation_payload,
    extract_deterministic_industry_signals,
)
from app.schemas.collusion import LLMIndustryProfileResult, LLMIndustrySignalResult


def _shrink_signal_context(text: str) -> str:
    """按 section 收缩行业信号上下文，保留完整块。

    问题3（轻量版）后，有招标文件时上下文退化为单块招标摘要，无法按 section
    删减。此时退化为按比例截断尾部，确保 input-too-long 重试确实缩短了载荷
    （否则重试发出等长请求会再次 400，形成无效重试）。
    """
    sections = [section.strip() for section in text.split("\n\n") if section.strip()]
    if len(sections) <= 1:
        if len(text) > 200:
            return text[: int(len(text) * 0.6)] + "\n（招标材料过长，已截断尾部）"
        return text
    kept = sections[:-1]
    kept.append("（以下行业线索已按优先级省略）")
    return "\n\n".join(kept)


class CollusionIndustryProfiler:
    """串标审查行业画像生成器

    Usage:
        profiler = CollusionIndustryProfiler(project_id=123)
        profile = await profiler.generate(tender_text="...")
    """

    def __init__(self, project_id: int) -> None:
        self._project_id = project_id
        self._client = get_review_client(project_id=project_id)

    async def generate(
        self,
        tender_text: Optional[str] = None,
        bid_extractions: Optional[list[dict]] = None,
    ) -> dict:
        """生成行业画像

        Args:
            tender_text: 招标文件文本（优先使用）
            bid_extractions: 多份投标文件的提取结果（无招标时同样使用）

        Returns:
            画像 dict，**始终返回 dict 而不是 None**，通过 `status` 字段区分四种状态:
            - "success": LLM 正常归纳出具体行业
            - "llm_returned_unknown": LLM 成功但归纳为 unknown
            - "insufficient_signals": 抽取到的信号不足以支撑画像
            - "llm_failed": LLM 调用超时或抛异常，含 failure_reason 详情
        """
        context = build_industry_signal_context(
            IndustryProfileContextInput(
                tender_text=tender_text,
                bid_extractions=bid_extractions or [],
            )
        )

        try:
            async def _send_request(payload: dict[str, str]):
                return await asyncio.wait_for(
                    self._client.chat_with_validation(
                        user_message=resolve_user_template(
                            "collusion.industry_signal", COLLUSION_INDUSTRY_SIGNAL_PROMPT
                        ).format(project_context=payload["context"]),
                        response_model=LLMIndustrySignalResult,
                        system_message=resolve_system_prompt(
                            "collusion.industry_signal", COLLUSION_INDUSTRY_SIGNAL_SYSTEM_PROMPT
                        ),
                        temperature=0.2,
                        max_tokens=1600,
                    ),
                    timeout=get_collusion_extract_timeout(),
                )

            signal_result = await retry_on_input_too_long_400(
                operation_name=f"串标行业信号(project={self._project_id})",
                initial_payload={"context": context},
                send_request=_send_request,
                reduce_payload_semantically=lambda payload, retry_round: (
                    {"context": _shrink_signal_context(payload["context"])},
                    {
                        "removed_segments": ["industry_signal_sections"],
                        "kept_segments": ["highest_priority_signal_sections"],
                        "retry_attempt": retry_round,
                    },
                ),
                max_retries=1,
                describe_payload=lambda payload: {
                    "context_chars": len(payload.get("context", "")),
                },
            )
            signal_payload = signal_result.model_dump()
            signals = signal_payload.get("signals") or []
            if not signals:
                signals = extract_deterministic_industry_signals(context)
            signal_payload["signals"] = signals
            signal_conf = str(signal_payload.get("confidence", "unknown")).lower()
            adequacy = assess_industry_signal_adequacy(signals)
            if not adequacy.is_sufficient:
                profile = {
                    "industry_type": "unknown",
                    "confidence": adequacy.confidence if signals else signal_conf,
                    "summary": "材料中的行业信号不足，行业画像已降级为 unknown。",
                    "standardized_domains": [],
                    "creative_domains": [],
                    "common_qualification_types": [],
                    "common_pricing_patterns": [],
                    "source_mode": "insufficient",
                    "signals": signals,
                    "signal_count": len(signals),
                    "status": "insufficient_signals",
                    "failure_reason": adequacy.reason,
                }
                logger.info(
                    f"[IndustryProfiler] project={self._project_id}, "
                    f"industry=unknown, confidence={profile['confidence']}, signals={len(signals)}, "
                    f"status=insufficient_signals, reason={adequacy.reason}"
                )
                return profile

            profile_result = await asyncio.wait_for(
                self._client.chat_with_validation(
                    user_message=resolve_user_template(
                        "collusion.industry_profile_gen", COLLUSION_INDUSTRY_PROFILE_PROMPT_V2
                    ).format(
                        industry_signals=json.dumps(
                            build_profile_generation_payload(
                                signal_payload=signal_payload,
                                context=context,
                                adequacy=adequacy,
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                    response_model=LLMIndustryProfileResult,
                    system_message=resolve_system_prompt(
                        "collusion.industry_profile_gen", COLLUSION_INDUSTRY_PROFILE_SYSTEM_PROMPT
                    ),
                    temperature=0.2,
                    max_tokens=1800,
                ),
                timeout=get_collusion_extract_timeout(),
            )
            profile = profile_result.model_dump()
            profile["signals"] = signals
            profile["signal_count"] = len(signals)
            profile["confidence"] = _cap_profile_confidence(
                str(profile.get("confidence", "unknown")).lower(),
                adequacy.confidence,
            )
            industry_type_norm = str(profile.get("industry_type") or "").strip().lower()
            profile["status"] = (
                "llm_returned_unknown"
                if not industry_type_norm or industry_type_norm == "unknown"
                else "success"
            )
            profile["failure_reason"] = None
            if profile["status"] == "llm_returned_unknown" and adequacy.is_sufficient:
                fallback_profile = build_deterministic_profile_from_signals(
                    signals,
                    adequacy=adequacy,
                )
                if fallback_profile:
                    fallback_profile["signals"] = signals
                    fallback_profile["signal_count"] = len(signals)
                    fallback_profile["status"] = "success"
                    fallback_profile["failure_reason"] = "llm_returned_unknown"
                    profile = fallback_profile
            std_count = len(profile.get("standardized_domains") or [])
            cre_count = len(profile.get("creative_domains") or [])
            logger.info(
                f"[IndustryProfiler] project={self._project_id}, "
                f"industry={profile.get('industry_type', '?')}, "
                f"confidence={profile.get('confidence', '?')}, "
                f"signals={len(signals)}, standardized={std_count}, creative={cre_count}, "
                f"status={profile['status']}"
            )
            return profile
        except asyncio.TimeoutError:
            logger.warning(
                f"[IndustryProfiler] timeout, project={self._project_id}"
            )
            return self._build_failed_profile(failure_reason="timeout")
        except Exception as e:
            # 收敛失败原因为 "exception:{ClassName}"，禁止泄漏堆栈细节
            logger.warning(
                f"[IndustryProfiler] failed: {e}, project={self._project_id}"
            )
            return self._build_failed_profile(failure_reason=f"exception:{type(e).__name__}")

    def _build_failed_profile(self, *, failure_reason: str) -> dict:
        """LLM 调用失败时的占位画像，保持下游消费契约稳定。"""
        return {
            "industry_type": "unknown",
            "confidence": "unknown",
            "summary": "行业画像生成失败，未能形成行业先验。",
            "standardized_domains": [],
            "creative_domains": [],
            "common_qualification_types": [],
            "common_pricing_patterns": [],
            "source_mode": "failed",
            "signals": [],
            "signal_count": 0,
            "status": "llm_failed",
            "failure_reason": failure_reason,
        }


def _cap_profile_confidence(profile_confidence: str, adequacy_confidence: str) -> str:
    """用输入信号充分性约束画像置信度，防止单点证据被放大。"""
    rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    labels = {value: key for key, value in rank.items()}
    profile_rank = rank.get(profile_confidence, 0)
    adequacy_rank = rank.get(adequacy_confidence, 0)
    return labels[min(profile_rank, adequacy_rank)]
