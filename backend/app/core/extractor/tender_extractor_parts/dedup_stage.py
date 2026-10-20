"""TenderExtractor 跨类去重 + 本地去重阶段（R2-2 拆分产出）。

承接原 `tender_extractor.py` 中以下 4 个方法：
- `_cross_category_dedup`  跨类别反思链路（LLM 通道 + 本地通道 + 合并 issues → secondary_merge）
- `_handle_local_issues`   按 canonical_key+subject_scope 分组保留代表项
- `_simple_dedupe`         描述相似度 + 关键词重叠的简单去重
- `_is_similar`            Jaccard 相似度判定

设计：函数第一参数 `self` 保留，函数名相对原 method **去前导下划线**，
body 与原 method **字面一致**。Prompt registry / settings 通过模块顶部直接 import，
与原文件相同（兼容 test_tender_extractor_*.py 的 sys.modules 假模块注入）。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from loguru import logger

from app.core.ai.prompts.tender_extract import get_reflection_prompt
from app.core.config import settings
from app.core.extractor.tender_extractor_parts.candidate_gate import (
    prefetch_candidate_embeddings,
)
from app.core.extractor.tender_extractor_parts.dedup_normalize import normalize_dedup_key
from app.schemas.requirement import ExtractedRequirement

if TYPE_CHECKING:
    from app.core.extractor.tender_extractor import TenderExtractor


async def cross_category_dedup(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """跨类别重复检测（反思机制）

    检查合并结果质量，必要时进行二次合并。

    Args:
        requirements: 分类合并后的要求列表

    Returns:
        list[ExtractedRequirement]: 最终去重后的要求列表
    """
    local_issues = self._check_quality(requirements)
    if not local_issues:
        logger.info("质量检查通过，无需二次合并")
        return requirements

    logger.warning(f"检测到 {len(local_issues)} 个质量问题，尝试反思+二次合并")
    self._report_progress(
        "merging",
        82,
        100,
        "反思链路分析中",
        substage="reflection",
        meta={"local_issue_count": len(local_issues)},
    )

    warmup_task: asyncio.Task | None = None
    if settings.TENDER_CANDIDATE_GATE_ENABLED:
        warmup_task = asyncio.create_task(
            prefetch_candidate_embeddings(
                [req.description or "" for req in requirements]
            ),
            name="tender-candidate-embed-warmup",
        )

    try:
        return await reflect_then_secondary_merge(
            self,
            requirements,
            local_issues,
            warmup_task,
        )
    finally:
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
            try:
                await warmup_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("候选向量预热任务回收时忽略异常")


async def reflect_then_secondary_merge(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    local_issues: list[dict],
    warmup_task: asyncio.Task | None,
) -> list[ExtractedRequirement]:
    """
    职责: 执行反思 LLM，等待候选向量预热，再进入精度保护二次合并。
    不做: 不判定是否点燃反思、不执行编码、不替代二次合并决策。
    """
    llm_issues: list[dict] = []
    quality_score = 100
    max_attempts = 1 + max(0, settings.TENDER_REFLECTION_JSON_RETRY)
    requirements_json = json.dumps(
        self._sanitize_requirements_for_prompt(requirements, merge_mode=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    last_reflection_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            system_msg, user_msg = get_reflection_prompt(
                requirements_json=requirements_json,
                count=len(requirements),
            )
            temperature = 0.1 if attempt == 0 else 0.0
            max_tokens = settings.TENDER_REFLECTION_MAX_TOKENS
            if attempt > 0:
                if last_reflection_error is not None and self._is_truncation_error(last_reflection_error):
                    max_tokens = min(int(max_tokens * 1.3), settings.TENDER_REFLECTION_MAX_TOKENS)
                else:
                    max_tokens = max(1200, int(max_tokens * 0.75))
                    user_msg += (
                        "\n\n【输出格式强约束】只输出 JSON 对象本体，不要 markdown 代码块、"
                        "不要解释文字、不要前后缀。issues 的 affected_indices 仅允许整数数组。"
                    )

            response = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if isinstance(response, dict):
                has_issues = bool(response.get("has_issues"))
                quality_score = int(response.get("quality_score", 100))
                raw_issues = response.get("issues", [])
                if has_issues and isinstance(raw_issues, list):
                    llm_issues = [
                        item for item in raw_issues
                        if isinstance(item, dict) and isinstance(item.get("affected_indices", []), list)
                    ]
            logger.info(
                "LLM反思成功: attempt={}/{} quality_score={} llm_issue_count={}",
                attempt + 1,
                max_attempts,
                quality_score,
                len(llm_issues),
            )
            break
        except Exception as e:
            last_reflection_error = e
            logger.warning(
                "LLM反思失败: attempt={}/{} error={}",
                attempt + 1,
                max_attempts,
                e,
            )
            self._record_stage_failure(
                "reflection",
                "llm_reflection_failed",
                attempt=attempt + 1,
                error=str(e)[:240],
            )
            if attempt >= max_attempts - 1 or not self._is_json_like_error(e):
                self._record_fallback(
                    "reflection",
                    "llm_reflection_unavailable",
                    local_issue_count=len(local_issues),
                )
                break

    merged_issues = list(local_issues)
    if llm_issues and quality_score < settings.TENDER_REFLECTION_QUALITY_THRESHOLD:
        merged_issues = self._merge_issue_lists(local_issues, llm_issues)

    if warmup_task is not None and not warmup_task.done():
        self._report_progress(
            "merging",
            84,
            100,
            "候选语义压缩中",
            substage="candidate_embed",
            meta={"local_issue_count": len(local_issues)},
        )
    if warmup_task is not None:
        try:
            await warmup_task
        except Exception as exc:
            logger.warning("候选向量预热失败，二次合并将按需编码: {}", exc)

    self._report_progress(
        "merging",
        84,
        100,
        "反思链路完成，执行精度保护合并",
        substage="reflection",
        meta={
            "local_issue_count": len(local_issues),
            "llm_issue_count": len(llm_issues),
            "merged_issue_count": len(merged_issues),
            "quality_score": quality_score,
        },
    )
    return await self._secondary_merge(requirements, merged_issues)


def handle_local_issues(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
) -> list[ExtractedRequirement]:
    """根据本地检测的问题进行简单去重

    Args:
        requirements: 要求列表
        issues: 问题列表

    Returns:
        list[ExtractedRequirement]: 处理后的要求列表
    """
    if not requirements:
        return requirements

    keep_indices: set[int] = set(range(len(requirements)))
    for issue in issues:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list) or len(affected) <= 1:
            continue

        groups: dict[str, list[int]] = {}
        for idx in affected:
            if not isinstance(idx, int) or idx < 0 or idx >= len(requirements):
                continue
            req = requirements[idx]
            canonical = req.canonical_key or self._infer_canonical_key(req.description, req.keywords)
            scope = self._infer_subject_scope(req.description, req.keywords)
            group_key = f"{canonical}|{scope}"
            groups.setdefault(group_key, []).append(idx)

        for _, idxs in groups.items():
            if len(idxs) <= 1:
                continue
            best = max(idxs, key=lambda i: len(requirements[i].description))
            for i in idxs:
                if i != best:
                    keep_indices.discard(i)

    result = [req for i, req in enumerate(requirements) if i in keep_indices]
    removed = len(requirements) - len(result)
    if removed > 0:
        logger.info(f"本地去重(语义保留): 移除 {removed} 条重复项")
    return self._apply_performance_alignment(result)


def simple_dedupe(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """简单去重（基于描述相似度和关键词匹配）。"""
    unique: list[ExtractedRequirement] = []
    seen_descriptions: list[str] = []
    seen_keywords: list[set[str]] = []

    for req in requirements:
        normalized = req.description.strip().lower()
        req_keywords = set(kw.lower() for kw in (req.keywords or []))

        duplicate_idx: int | None = None
        for i, seen in enumerate(seen_descriptions):
            similar = self._is_similar(normalized, seen, threshold=0.5)
            if not similar and req_keywords and seen_keywords[i]:
                overlap = len(req_keywords & seen_keywords[i])
                min_size = min(len(req_keywords), len(seen_keywords[i]))
                similar = min_size > 0 and overlap / min_size >= 0.8
            if not similar:
                continue
            from app.core.extractor.mandatory.signals import (
                atomic_conditions_must_stay_apart,
            )

            if atomic_conditions_must_stay_apart(
                req.description, unique[i].description
            ):
                continue
            duplicate_idx = i
            break

        if duplicate_idx is None:
            seen_descriptions.append(normalized)
            seen_keywords.append(req_keywords)
            unique.append(req)
        else:
            existing = unique[duplicate_idx]
            replace_existing = (
                (existing.is_mandatory is False and req.is_mandatory is True)
                or len(req.description) > len(existing.description)
            )
            if replace_existing:
                merged = self._merge_provenance(req, existing)
                seen_descriptions[duplicate_idx] = merged.description.strip().lower()
            else:
                merged = self._merge_provenance(existing, req)
            # 否决项不可因"非否决项描述更长"被降级：合并结果只要任一来源为否决项即保留
            if (existing.is_mandatory or req.is_mandatory) and not merged.is_mandatory:
                merged = merged.model_copy(update={"is_mandatory": True})
            unique[duplicate_idx] = merged
            seen_keywords[duplicate_idx] = set(
                kw.lower() for kw in (merged.keywords or [])
            )

    if len(requirements) > len(unique):
        logger.debug(f"简单去重: {len(requirements)} -> {len(unique)}")

    return self._apply_performance_alignment(unique)


def terminal_exact_collapse(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """入库前确定性终检去重。

 职责: 仅合并"同类别 + 同子面(subject_scope) + 同子要求(sub_requirements) +
 描述逐字相同"的冗余条目，作为各 happy-path 漏检后的最后收口。
 不做: 模糊相似合并（避免误并不同要求）；不跨 subject_scope/sub_requirements
 合并（避免回收 _split_compound_requirements 的复合拆分，防 回归）。
 """
    survivors: list[ExtractedRequirement] = []
    index_by_key: dict[tuple, int] = {}

    for req in requirements:
        norm_desc = normalize_dedup_key(req.description)
        key = (
            req.category,
            req.subject_scope or "",
            tuple(req.sub_requirements or []),
            norm_desc,
        )
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            index_by_key[key] = len(survivors)
            survivors.append(req)
            continue

        merged = self._merge_provenance(survivors[existing_idx], req)
        # 否决项不可因合并被丢失：任一来源为否决项即保留
        if (survivors[existing_idx].is_mandatory or req.is_mandatory) and not merged.is_mandatory:
            merged = merged.model_copy(update={"is_mandatory": True})
        survivors[existing_idx] = merged

    removed = len(requirements) - len(survivors)
    if removed > 0:
        logger.info(f"终检确定性去重: 移除 {removed} 条逐字重复项")
    return survivors


def is_similar(self: "TenderExtractor", text1: str, text2: str, threshold: float = 0.5) -> bool:
    """判断两个文本是否相似

    使用 Jaccard 相似度（字符集合重叠率）计算。

    Args:
        text1: 文本1
        text2: 文本2
        threshold: 相似度阈值（默认 0.5，适合中文短文本）

    Returns:
        bool: 是否相似
    """
    if not text1 or not text2:
        return False

    clean1 = re.sub(r'[\s\W]', '', text1)
    clean2 = re.sub(r'[\s\W]', '', text2)

    if not clean1 or not clean2:
        return False

    set1 = set(clean1)
    set2 = set(clean2)

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    if union == 0:
        return False

    similarity = intersection / union
    return similarity >= threshold
