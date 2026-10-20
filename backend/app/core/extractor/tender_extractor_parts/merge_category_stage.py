"""TenderExtractor 类别合并阶段（R2-2 拆分产出）。

承接原 `tender_extractor.py` 中以下 6 个方法：
- `_merge_category`                  分类合并路由器（小输入 single_pass / 大输入 batched）
- `_estimate_category_max_tokens`    决策模式 token 上限估算
- `_build_category_batches`          按 TENDER_CATEGORY_BATCH_SIZE 顺序切分
- `_select_category_high_risk_indices`  高风险条目打分裁剪（重试缩减规模）
- `_merge_category_single_pass`      单轮 LLM 决策合并 + JSON 失败补救
- `_merge_category_batched`          分批并行合并 + 最终收敛 (single / batched)

设计：函数第一参数 `self` 保留，函数名相对原 method **去前导下划线**，
body 与原 method **字面一致**。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING, Optional

from loguru import logger

from app.core.ai.prompts.tender_extract import get_category_merge_decision_prompt
from app.core.config import settings
from app.schemas.requirement import ExtractedRequirement

if TYPE_CHECKING:
    from app.core.extractor.tender_extractor import TenderExtractor


async def merge_category(
    self: "TenderExtractor",
    category: str,
    requirements: list[ExtractedRequirement],
    *,
    on_batch_progress: Optional[Callable[[int, int], None]] = None,
) -> list[ExtractedRequirement]:
    """对单个类别内的要求进行 LLM 合并

    上下文更聚焦，合并效果更好。

    Args:
        category: 类别代码
        requirements: 该类别的要求列表

    Returns:
        list[ExtractedRequirement]: 合并后的要求列表
    """
    if not requirements:
        return []

    batch_trigger = max(2, settings.TENDER_CATEGORY_BATCH_TRIGGER)
    if category == "TECH":
        batch_trigger = min(
            batch_trigger,
            max(2, settings.TENDER_CATEGORY_TECH_FORCE_BATCH_THRESHOLD),
        )

    if len(requirements) >= batch_trigger:
        return await self._merge_category_batched(
            category, requirements, on_batch_progress=on_batch_progress
        )

    merged = await self._merge_category_single_pass(
        category=category,
        requirements=requirements,
        final_pass=False,
        allow_retry=True,
        pass_label=f"category-single-{category}",
    )
    if merged is not None:
        return merged

    logger.info("分类合并失败后回退简单去重: category={}", category)
    self._record_fallback(
        "category_merge",
        "category_single_failed",
        category=category,
        input_count=len(requirements),
    )
    return self._simple_dedupe(requirements)


def estimate_category_max_tokens(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    *,
    final_pass: bool = False,
) -> int:
    """基于决策输出模式估算分类合并 token 上限。"""
    estimated_groups = max(3, len(requirements) // 3)
    estimated_tokens = max(1500, estimated_groups * 80)
    cap = (
        settings.TENDER_CATEGORY_FINAL_PASS_MAX_TOKENS
        if final_pass
        else settings.TENDER_CATEGORY_MAX_TOKENS
    )
    return min(estimated_tokens, max(2000, cap))


def build_category_batches(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[list[ExtractedRequirement]]:
    """按输入顺序构建类别内分批。"""
    if not requirements:
        return []

    batch_size = max(2, settings.TENDER_CATEGORY_BATCH_SIZE)
    return [
        requirements[idx:idx + batch_size]
        for idx in range(0, len(requirements), batch_size)
    ]


def select_category_high_risk_indices(
    self: "TenderExtractor",
    category: str,
    requirements: list[ExtractedRequirement],
) -> list[int]:
    """重试时优先保留高风险条目，降低输入规模但不丢核心语义。"""
    if not requirements:
        return []
    if len(requirements) <= 6:
        return list(range(len(requirements)))

    category_markers = {
        "TECH": {"测试", "方案", "参数", "验收", "标准"},
        "QUAL": {"资质", "执照", "证书", "法人"},
        "COMM": {"报价", "价格", "保证金", "付款", "税率"},
        "PERF": {"业绩", "合同", "金额", "项目"},
        "STAFF": {"项目经理", "负责人", "人员", "任职"},
        "FORMAT": {"签章", "盖章", "格式", "份数"},
    }
    marker_set = category_markers.get(category, set())
    scored: list[tuple[int, int]] = []
    for idx, req in enumerate(requirements):
        score = 0
        if req.is_mandatory:
            score += 5
        text = f"{req.description} {' '.join(req.keywords or [])}"
        if any(m in text for m in marker_set):
            score += 4
        score += min(3, len(req.keywords or []))
        score += min(5, len(req.description) // 40)
        scored.append((score, idx))

    scored.sort(key=lambda item: (-item[0], item[1]))
    keep_count = max(6, int(len(requirements) * 0.7))
    selected = sorted(idx for _, idx in scored[:keep_count])
    return selected


async def merge_category_single_pass(
    self: "TenderExtractor",
    category: str,
    requirements: list[ExtractedRequirement],
    *,
    final_pass: bool = False,
    allow_retry: bool = True,
    pass_label: str = "category-single-pass",
) -> list[ExtractedRequirement] | None:
    """执行一轮分类合并（含局部JSON补救重试）。"""
    if not requirements:
        return []

    max_attempts = 1 + (
        settings.TENDER_CATEGORY_JSON_RETRY if allow_retry else 0
    )
    last_error: Exception | None = None
    token_cap = (
        settings.TENDER_CATEGORY_FINAL_PASS_MAX_TOKENS
        if final_pass
        else settings.TENDER_CATEGORY_MAX_TOKENS
    )

    for attempt in range(max_attempts):
        working_requirements = requirements
        untouched_requirements: list[ExtractedRequirement] = []
        reduced = False
        if attempt > 0 and len(requirements) > max(8, settings.TENDER_CATEGORY_BATCH_SIZE):
            selected_indices = self._select_category_high_risk_indices(category, requirements)
            if 0 < len(selected_indices) < len(requirements):
                selected_index_set = set(selected_indices)
                working_requirements = [requirements[i] for i in selected_indices]
                untouched_requirements = [
                    req for idx, req in enumerate(requirements) if idx not in selected_index_set
                ]
                reduced = True

        requirements_json = json.dumps(
            self._sanitize_requirements_for_prompt(working_requirements, merge_mode=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_msg, user_msg = get_category_merge_decision_prompt(
            category=category,
            requirements_json=requirements_json,
            count=len(working_requirements),
        )
        temperature = 0.1
        max_tokens = self._estimate_category_max_tokens(
            requirements=working_requirements,
            final_pass=final_pass,
        )

        if attempt > 0:
            temperature = 0.0
            if last_error is not None and self._is_truncation_error(last_error):
                truncation_pos = self._parse_truncation_char_pos(last_error)
                if truncation_pos:
                    consumed_est = truncation_pos / 2.5
                    scale = max(1.3, max_tokens / max(1, consumed_est))
                    max_tokens = min(int(max_tokens * scale), token_cap)
            else:
                max_tokens = max(1500, int(max_tokens * 0.75))
                user_msg += (
                    "\n\n【输出格式强约束】只输出 JSON 数组本体，不要 markdown 代码块，"
                    "不要解释文字、不要前后缀。每个元素必须包含 "
                    "merge_ids/category/description/is_mandatory/keywords 字段。"
                )
            if reduced:
                user_msg += "\n【收敛策略】当前为高风险子集收敛轮，请优先合并实质性重复。"

        # 每次 LLM 调用前检查项目取消，避免墓碑写入后仍继续分类合并
        from app.core.tasks.cancellation import invoke_cancel_check

        invoke_cancel_check(getattr(self, "_cancel_check", None))
        start_ts = perf_counter()
        try:
            response = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            merged = self._normalize_merge_decision_response(
                response, working_requirements
            )
            elapsed_ms = int((perf_counter() - start_ts) * 1000)
            if merged is None:
                raise ValueError("分类合并返回格式不符合预期")
            if untouched_requirements:
                merged = self._simple_dedupe([*merged, *untouched_requirements])

            usage = getattr(self.llm_client, "_last_usage", None) or {}
            logger.info(
                "分类合并成功: stage=category_merge category={} pass={} attempt={}/{} in={} out={} max_tokens={} elapsed_ms={} reduced_input={}"
                " prompt_chars={} prompt_tokens={} completion_tokens={}",
                category,
                pass_label,
                attempt + 1,
                max_attempts,
                len(working_requirements),
                len(merged),
                max_tokens,
                elapsed_ms,
                reduced,
                len(user_msg),
                usage.get("prompt_tokens", "N/A"),
                usage.get("completion_tokens", "N/A"),
            )
            return merged
        except Exception as e:
            last_error = e
            elapsed_ms = int((perf_counter() - start_ts) * 1000)
            logger.warning(
                "分类合并失败: stage=category_merge category={} pass={} attempt={}/{} in={} max_tokens={} elapsed_ms={} reduced_input={} error={}",
                category,
                pass_label,
                attempt + 1,
                max_attempts,
                len(working_requirements),
                max_tokens,
                elapsed_ms,
                reduced,
                e,
            )
            self._record_stage_failure(
                "category_merge",
                "llm_call_failed",
                category=category,
                pass_label=pass_label,
                attempt=attempt + 1,
                input_count=len(working_requirements),
                reduced_input=reduced,
                error=str(e)[:240],
            )
            if attempt >= max_attempts - 1 or not self._is_json_like_error(e):
                break

    return None


async def merge_category_batched(
    self: "TenderExtractor",
    category: str,
    requirements: list[ExtractedRequirement],
    *,
    on_batch_progress: Optional[Callable[[int, int], None]] = None,
) -> list[ExtractedRequirement]:
    """类别内分批合并，降低超长JSON输出失败风险。"""
    batches = self._build_category_batches(requirements)
    if not batches:
        return []

    logger.info(
        "分类合并分批启动: stage=category_merge category={} total_requirements={} batch_count={} batch_size={}",
        category,
        len(requirements),
        len(batches),
        settings.TENDER_CATEGORY_BATCH_SIZE,
    )

    cat_concurrency = max(1, settings.TENDER_CATEGORY_MERGE_CONCURRENCY)
    cat_semaphore = asyncio.Semaphore(cat_concurrency)
    cat_results_map: dict[int, list[ExtractedRequirement]] = {}
    cat_completed = 0
    cat_total = len(batches)

    async def _process_category_batch(
        b_idx: int,
        b_reqs: list[ExtractedRequirement],
    ) -> tuple[int, list[ExtractedRequirement], bool]:
        async with cat_semaphore:
            merged = await self._merge_category_single_pass(
                category=category,
                requirements=b_reqs,
                final_pass=False,
                allow_retry=True,
                pass_label=f"category-batch-{b_idx}",
            )
            fb = merged is None
            if merged is None:
                self._record_fallback(
                    "category_merge",
                    "category_batch_failed",
                    category=category,
                    batch_idx=b_idx,
                    batch_count=cat_total,
                )
                merged = self._simple_dedupe(b_reqs)
            return b_idx, merged, fb

    cat_tasks = [
        _process_category_batch(idx, reqs)
        for idx, reqs in enumerate(batches, start=1)
    ]
    for coro in asyncio.as_completed(cat_tasks):
        batch_idx, merged, fallback_used = await coro
        cat_results_map[batch_idx] = merged
        cat_completed += 1
        logger.info(
            "分类合并批次完成: stage=category_merge category={} batch={}/{} out={} fallback_used={}",
            category,
            batch_idx,
            cat_total,
            len(merged),
            fallback_used,
        )
        if on_batch_progress is not None:
            on_batch_progress(cat_completed, cat_total)

    merged_results: list[ExtractedRequirement] = []
    for idx in sorted(cat_results_map):
        merged_results.extend(cat_results_map[idx])

    if len(merged_results) > max(2, settings.TENDER_CATEGORY_BATCH_SIZE):
        final_single_limit = max(1, settings.TENDER_CATEGORY_FINAL_PASS_SINGLE_MAX_INPUT)
        if len(merged_results) <= final_single_limit:
            final_merged = await self._merge_category_single_pass(
                category=category,
                requirements=merged_results,
                final_pass=True,
                allow_retry=True,
                pass_label="category-final-pass",
            )
            if final_merged is not None:
                return final_merged

            self._record_fallback(
                "category_merge",
                "category_final_single_failed",
                category=category,
                input_count=len(merged_results),
            )
            logger.info(
                "分类最终收敛失败，回退本地收敛: stage=category_merge category={} mode=single",
                category,
            )
            return self._simple_dedupe(merged_results)

        logger.info(
            "分类最终收敛触发分批门控: stage=category_merge category={} input_count={} single_limit={}",
            category,
            len(merged_results),
            final_single_limit,
        )
        final_batches = self._build_secondary_batches(
            merged_results,
            self._check_quality(merged_results),
            batch_size=settings.TENDER_CATEGORY_FINAL_PASS_BATCH_SIZE,
        )
        final_results: list[ExtractedRequirement] = []
        for batch_idx, (batch_requirements, batch_issues) in enumerate(final_batches, start=1):
            final_merged = await self._secondary_merge_single_pass(
                requirements=batch_requirements,
                issues=batch_issues,
                final_pass=True,
                allow_retry=True,
                pass_label=f"category-final-batch-{category}-{batch_idx}",
            )
            if final_merged is None:
                local_issues = batch_issues or self._check_quality(batch_requirements)
                final_merged = self._handle_local_issues(batch_requirements, local_issues)
                self._record_fallback(
                    "category_merge",
                    "category_final_batch_failed",
                    category=category,
                    batch_idx=batch_idx,
                    batch_count=len(final_batches),
                )
            final_results.extend(final_merged)

        final_results = self._simple_dedupe(final_results)
        if final_results:
            return final_results

        self._record_fallback(
            "category_merge",
            "category_final_batched_empty",
            category=category,
        )

    return self._simple_dedupe(merged_results)
