"""TenderExtractor 二次合并阶段（R2-2 拆分产出）。

承接原 `tender_extractor.py` 中以下 13 个方法：
- `_secondary_merge`                     路由器（单轮 vs 分批）
- `_estimate_secondary_max_tokens`       token 上限估算
- `_is_json_like_error`                  JSON 错误识别
- `_is_truncation_error`                 截断错误识别
- `_parse_truncation_char_pos`           截断位置提取
- `_normalize_secondary_response`        响应格式标准化
- `_apply_merge_decisions`               LLM 决策本地合并执行
- `_normalize_merge_decision_response`   决策模式响应规范化
- `_build_secondary_batches_by_budget`   token budget + union-find 智能分桶
- `_build_secondary_batches`             固定大小分桶 (回退)
- `_reduce_secondary_input_for_retry`    final-pass 重试规模缩减
- `_secondary_merge_single_pass`         单轮 LLM 二次合并
- `_secondary_merge_batched`             分批并行 + 精准最终收敛 + 兜底

设计：函数第一参数 `self` 保留，函数名相对原 method **去前导下划线**，
body 与原 method **字面一致**。
"""

from __future__ import annotations

import asyncio
import json
import re
from time import perf_counter
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from app.core.ai.prompts.tender_extract import get_secondary_merge_decision_prompt
from app.core.config import settings
from app.core.extractor.tender_extractor_parts.candidate_gate import (
    partition_requirements_with_embeddings,
)
from app.schemas.requirement import ExtractedRequirement

if TYPE_CHECKING:
    from app.core.extractor.tender_extractor import TenderExtractor


async def secondary_merge(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
) -> list[ExtractedRequirement]:
    """根据问题进行二次合并。"""
    trigger = max(1, settings.TENDER_SECONDARY_BATCH_TRIGGER)
    if len(requirements) >= trigger:
        return await self._secondary_merge_batched(requirements, issues)

    merged = await self._secondary_merge_single_pass(
        requirements=requirements,
        issues=issues,
        final_pass=False,
        allow_retry=True,
        pass_label="single-pass",
    )
    if merged is not None:
        return merged

    logger.info("二次合并失败，使用本地去重回退")
    self._record_fallback(
        "secondary_merge",
        "secondary_single_failed",
        input_count=len(requirements),
    )
    return self._handle_local_issues(requirements, issues)


def estimate_secondary_max_tokens(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    final_pass: bool = False,
) -> int:
    """基于决策输出模式估算二次合并 token 上限。"""
    estimated_groups = max(3, len(requirements) // 3)
    estimated_tokens = max(1500, estimated_groups * 80)
    cap = (
        settings.TENDER_SECONDARY_FINAL_PASS_MAX_TOKENS
        if final_pass
        else settings.TENDER_SECONDARY_MAX_TOKENS
    )
    return min(estimated_tokens, max(1500, cap))


def is_json_like_error(self: "TenderExtractor", error: Exception) -> bool:
    """判断是否属于JSON结构化失败。"""
    if isinstance(error, (json.JSONDecodeError, ValidationError)):
        return True

    text = str(error).lower()
    markers = (
        "json",
        "unterminated string",
        "expecting ',' delimiter",
        "expecting value",
        "invalid control character",
        "extra data",
    )
    return any(marker in text for marker in markers)


def is_truncation_error(self: "TenderExtractor", error: Exception) -> bool:
    """判断是否为 max_tokens 截断导致的 JSON 不完整。"""
    return "unterminated string" in str(error).lower()


def parse_truncation_char_pos(self: "TenderExtractor", error: Exception) -> int | None:
    """从截断类错误中提取被截断的字符位置。"""
    match = re.search(r"\(char\s+(\d+)\)", str(error))
    return int(match.group(1)) if match else None


def normalize_secondary_response(
    self: "TenderExtractor",
    response: Any,
) -> list[ExtractedRequirement] | None:
    """标准化二次合并响应格式。"""
    if isinstance(response, list):
        return [ExtractedRequirement(**item) for item in response]
    if isinstance(response, dict) and "requirements" in response:
        return [ExtractedRequirement(**item) for item in response["requirements"]]
    return None


def apply_merge_decisions(
    self: "TenderExtractor",
    original_requirements: list[ExtractedRequirement],
    decisions: list[dict],
) -> list[ExtractedRequirement]:
    """根据 LLM 合并决策在本地执行合并操作。

    未出现在任何 merge_ids 中的条目自动原样保留。
    """
    merged_indices: set[int] = set()
    new_requirements: list[ExtractedRequirement] = []

    for decision in decisions:
        merge_ids = decision.get("merge_ids", [])
        if not isinstance(merge_ids, list) or len(merge_ids) < 2:
            continue

        valid_ids = [
            idx for idx in merge_ids
            if isinstance(idx, int) and 0 <= idx < len(original_requirements)
        ]
        if len(valid_ids) < 2:
            continue

        from app.core.extractor.mandatory.signals import (
            atomic_conditions_must_stay_apart,
        )

        originals = [original_requirements[i] for i in valid_ids]
        if any(
            atomic_conditions_must_stay_apart(left.description, right.description)
            for idx, left in enumerate(originals)
            for right in originals[idx + 1 :]
        ):
            continue

        merged_indices.update(valid_ids)

        all_source_pages: list[int] = []
        all_sub_requirements: list[str] = []
        best_req = max(
            (original_requirements[i] for i in valid_ids),
            key=lambda r: len(r.description),
        )
        for idx in valid_ids:
            orig = original_requirements[idx]
            all_source_pages.extend(orig.source_pages or [])
            all_sub_requirements.extend(orig.sub_requirements or [])

        merged_source_pages = sorted(set(all_source_pages))
        merged_sub = list(dict.fromkeys(all_sub_requirements))

        is_mandatory = decision.get("is_mandatory", False)
        if not is_mandatory:
            is_mandatory = any(
                original_requirements[i].is_mandatory for i in valid_ids
            )

        new_req = ExtractedRequirement(
            category=decision.get("category", best_req.category),
            description=decision.get("description", best_req.description),
            is_mandatory=is_mandatory,
            keywords=decision.get("keywords", best_req.keywords or []),
            source_pages=merged_source_pages,
            merged_count=len(valid_ids),
            sub_requirements=merged_sub,
            topic=best_req.topic,
            subject_scope=best_req.subject_scope,
            canonical_key=best_req.canonical_key,
        )
        new_requirements.append(new_req)

    kept = [
        req for idx, req in enumerate(original_requirements)
        if idx not in merged_indices
    ]
    return kept + new_requirements


def normalize_merge_decision_response(
    self: "TenderExtractor",
    response: Any,
    original_requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement] | None:
    """解析决策模式的 LLM 响应，自动兼容旧格式。"""
    if isinstance(response, list):
        if not response:
            return list(original_requirements)
        first = response[0]
        if isinstance(first, dict) and "merge_ids" in first:
            return self._apply_merge_decisions(original_requirements, response)
        return self._normalize_secondary_response(response)
    if isinstance(response, dict) and "requirements" in response:
        return self._normalize_secondary_response(response)
    return None


def build_secondary_batches_by_budget(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
    *,
    token_budget: int | None = None,
) -> list[tuple[list[ExtractedRequirement], list[dict]]]:
    """基于 token 预算 + issue 连通分量的智能分桶。

    比固定 batch_size 更均衡：用 union-find 保证关联条目不被拆散，
    用 token budget 控制每桶复杂度。
    """
    if not requirements:
        return []

    budget = max(500, token_budget if token_budget is not None else settings.TENDER_MERGE_TOKEN_BUDGET)
    n = len(requirements)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for issue in issues:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list):
            continue
        valid = [i for i in affected if isinstance(i, int) and 0 <= i < n]
        for i in range(1, len(valid)):
            union(valid[0], valid[i])

    def _estimate_tokens(req: ExtractedRequirement) -> int:
        return int(
            len(req.description or "") * 0.5
            + sum(len(kw) * 0.5 for kw in (req.keywords or []))
        )

    components: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        components.setdefault(root, []).append(i)

    comp_list = list(components.values())
    comp_costs = [
        sum(_estimate_tokens(requirements[i]) for i in comp)
        for comp in comp_list
    ]
    order = sorted(range(len(comp_list)), key=lambda x: comp_costs[x], reverse=True)

    bins: list[list[int]] = []
    bin_costs: list[int] = []

    for ci in order:
        comp = comp_list[ci]
        cost = comp_costs[ci]
        placed = False
        if cost <= budget:
            for bi in range(len(bins)):
                if bin_costs[bi] + cost <= budget:
                    bins[bi].extend(comp)
                    bin_costs[bi] += cost
                    placed = True
                    break
        if not placed:
            bins.append(list(comp))
            bin_costs.append(cost)

    if not bins:
        bins.append(list(range(n)))

    batches: list[tuple[list[ExtractedRequirement], list[dict]]] = []
    for bin_indices in bins:
        index_map = {old_idx: new_idx for new_idx, old_idx in enumerate(bin_indices)}
        batch_reqs = [requirements[i] for i in bin_indices]
        batch_issues: list[dict] = []
        for issue in issues:
            affected = issue.get("affected_indices", [])
            if not isinstance(affected, list):
                continue
            local = [index_map[i] for i in affected if i in index_map]
            if len(local) < 2:
                continue
            cloned = dict(issue)
            cloned["affected_indices"] = local
            batch_issues.append(cloned)
        batches.append((batch_reqs, batch_issues))

    return batches


def build_secondary_batches(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
    *,
    batch_size: int | None = None,
) -> list[tuple[list[ExtractedRequirement], list[dict]]]:
    """构建 issue-aware 的二次合并批次（旧版固定大小分桶，作为回退）。"""
    if not requirements:
        return []

    effective_batch_size = max(
        2,
        batch_size if batch_size is not None else settings.TENDER_SECONDARY_BATCH_SIZE,
    )

    priority_indices: list[int] = []
    seen: set[int] = set()
    for issue in issues:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list):
            continue
        for idx in affected:
            if (
                isinstance(idx, int)
                and 0 <= idx < len(requirements)
                and idx not in seen
            ):
                seen.add(idx)
                priority_indices.append(idx)

    remaining_indices = [i for i in range(len(requirements)) if i not in seen]
    ordered_indices = priority_indices + remaining_indices

    batches: list[tuple[list[ExtractedRequirement], list[dict]]] = []
    for start in range(0, len(ordered_indices), effective_batch_size):
        batch_indices = ordered_indices[start:start + effective_batch_size]
        index_map = {old_idx: new_idx for new_idx, old_idx in enumerate(batch_indices)}
        batch_requirements = [requirements[i] for i in batch_indices]

        batch_issues: list[dict] = []
        for issue in issues:
            affected = issue.get("affected_indices", [])
            if not isinstance(affected, list):
                continue
            local_affected = [index_map[i] for i in affected if i in index_map]
            if len(local_affected) < 2:
                continue
            cloned = dict(issue)
            cloned["affected_indices"] = local_affected
            batch_issues.append(cloned)

        batches.append((batch_requirements, batch_issues))

    return batches


def reduce_secondary_input_for_retry(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
    attempt: int,
) -> tuple[list[ExtractedRequirement], list[ExtractedRequirement], bool]:
    """final-pass重试时降规模（保留高风险子集 + 其余原样回并）。"""
    if len(requirements) <= 6:
        return requirements, [], False

    priority_indices: list[int] = []
    seen: set[int] = set()
    for issue in issues or []:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list):
            continue
        for idx in affected:
            if isinstance(idx, int) and 0 <= idx < len(requirements) and idx not in seen:
                seen.add(idx)
                priority_indices.append(idx)
    if not priority_indices:
        return requirements, [], False

    ratio = 0.75 if attempt == 1 else 0.6
    target_size = max(6, int(len(requirements) * ratio))
    selected = priority_indices[:target_size]
    if len(selected) < target_size:
        for idx in range(len(requirements)):
            if idx not in seen:
                selected.append(idx)
            if len(selected) >= target_size:
                break
    selected_set = set(selected)
    subset = [req for idx, req in enumerate(requirements) if idx in selected_set]
    untouched = [req for idx, req in enumerate(requirements) if idx not in selected_set]
    if not subset or len(subset) == len(requirements):
        return requirements, [], False
    return subset, untouched, True


async def secondary_merge_single_pass(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
    *,
    final_pass: bool = False,
    allow_retry: bool = True,
    pass_label: str = "single-pass",
) -> list[ExtractedRequirement] | None:
    """执行一轮二次合并（含局部JSON重试）。"""
    if not requirements:
        return []

    max_attempts = 1 + (
        settings.TENDER_SECONDARY_JSON_RETRY if allow_retry else 0
    )
    last_error: Exception | None = None
    token_cap = (
        settings.TENDER_SECONDARY_FINAL_PASS_MAX_TOKENS
        if final_pass
        else settings.TENDER_SECONDARY_MAX_TOKENS
    )

    for attempt in range(max_attempts):
        working_requirements = requirements
        untouched_requirements: list[ExtractedRequirement] = []
        reduced = False
        if final_pass and attempt > 0:
            working_requirements, untouched_requirements, reduced = self._reduce_secondary_input_for_retry(
                requirements=requirements,
                issues=issues,
                attempt=attempt,
            )

        requirements_json = json.dumps(
            self._sanitize_requirements_for_prompt(working_requirements, merge_mode=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        issues_json = json.dumps(issues, ensure_ascii=False, separators=(",", ":"))
        system_msg, user_msg = get_secondary_merge_decision_prompt(
            requirements_json=requirements_json,
            issues_json=issues_json,
        )
        temperature = 0.1
        max_tokens = self._estimate_secondary_max_tokens(
            requirements=working_requirements,
            final_pass=final_pass,
        )
        runtime_max_tokens = max_tokens
        if attempt > 0:
            temperature = 0.0
            if last_error is not None and self._is_truncation_error(last_error):
                truncation_pos = self._parse_truncation_char_pos(last_error)
                if truncation_pos:
                    consumed_est = truncation_pos / 2.5
                    scale = max(1.3, runtime_max_tokens / max(1, consumed_est))
                    runtime_max_tokens = min(int(runtime_max_tokens * scale), token_cap)
                _skip_format_constraint = True
            else:
                _skip_format_constraint = False
            if not _skip_format_constraint:
                user_msg += (
                    "\n\n【输出格式强约束】只输出 JSON 数组本体，不要 markdown 代码块，"
                    "不要解释文字，不要前后缀。每个元素必须包含 "
                    "merge_ids/category/description/is_mandatory/keywords 字段。"
                )
                if final_pass:
                    factor = 0.75 if attempt == 1 else 0.6
                    runtime_max_tokens = max(1200, int(max_tokens * factor))
            if reduced:
                user_msg += "\n【收敛策略】当前为高风险子集收敛轮，请优先保留关键约束语义。"

        start_ts = perf_counter()
        try:
            response = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=runtime_max_tokens,
            )
            merged = self._normalize_merge_decision_response(
                response, working_requirements
            )
            elapsed_ms = int((perf_counter() - start_ts) * 1000)
            if merged is None:
                raise ValueError("二次合并返回格式不符合预期")
            if untouched_requirements:
                merged = self._simple_dedupe([*merged, *untouched_requirements])

            usage = getattr(self.llm_client, "_last_usage", None) or {}
            logger.info(
                "二次合并成功: stage=secondary_merge pass={} attempt={}/{} in={} out={} max_tokens={} elapsed_ms={} reduced_input={}"
                " prompt_chars={} prompt_tokens={} completion_tokens={}",
                pass_label,
                attempt + 1,
                max_attempts,
                len(working_requirements),
                len(merged),
                runtime_max_tokens,
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
                "二次合并失败: stage=secondary_merge pass={} attempt={}/{} in={} max_tokens={} elapsed_ms={} reduced_input={} error={}",
                pass_label,
                attempt + 1,
                max_attempts,
                len(working_requirements),
                runtime_max_tokens,
                elapsed_ms,
                reduced,
                e,
            )
            self._record_stage_failure(
                "secondary_merge",
                "llm_call_failed",
                pass_label=pass_label,
                attempt=attempt + 1,
                input_count=len(working_requirements),
                reduced_input=reduced,
                error=str(e)[:240],
            )
            if attempt >= max_attempts - 1 or not self._is_json_like_error(e):
                break

    return None


async def secondary_merge_batched(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
    issues: list[dict],
) -> list[ExtractedRequirement]:
    """分批执行二次合并，降低超长JSON输出失败风险。"""
    safe_prefix: list[ExtractedRequirement] = []
    if settings.TENDER_CANDIDATE_GATE_ENABLED and issues:
        affected_set = _collect_affected_indices(issues, len(requirements))
        if affected_set:
            partition = await partition_requirements_with_embeddings(
                requirements,
                fallback_recorder=self._record_fallback,
            )
            selected = set(affected_set)
            for group in partition.gate_result.components:
                if any(idx in affected_set for idx in group):
                    selected.update(group)
            if 0 < len(selected) < len(requirements):
                ordered_selected = sorted(selected)
                index_map = {old: new for new, old in enumerate(ordered_selected)}
                safe_prefix = [
                    req for idx, req in enumerate(requirements)
                    if idx not in selected
                ]
                requirements = [requirements[idx] for idx in ordered_selected]
                issues = _remap_issues(issues, index_map)
                logger.info(
                    "二次合并候选门控: total={} target={} safe={}",
                    len(safe_prefix) + len(requirements),
                    len(requirements),
                    len(safe_prefix),
                )

    try:
        batches = self._build_secondary_batches_by_budget(requirements, issues)
    except Exception as e:
        logger.warning("智能分桶失败, 回退固定分桶: {}", e)
        batches = self._build_secondary_batches(requirements, issues)
    if not batches:
        return requirements

    logger.info(
        "二次合并分批启动: stage=secondary_merge total_requirements={} batch_count={} batch_size={}",
        len(requirements),
        len(batches),
        settings.TENDER_SECONDARY_BATCH_SIZE,
    )
    self._report_progress(
        "merging",
        85,
        100,
        f"二次合并分批启动（{len(batches)}批）",
        substage="secondary_batch",
        meta={"batch_count": len(batches), "total_input": len(requirements)},
    )

    concurrency = max(1, settings.TENDER_SECONDARY_MERGE_CONCURRENCY)
    semaphore = asyncio.Semaphore(concurrency)
    results_map: dict[int, list[ExtractedRequirement]] = {}
    completed_count = 0
    total_batches = len(batches)

    async def _process_secondary_batch(
        b_idx: int,
        b_reqs: list[ExtractedRequirement],
        b_issues: list[dict],
    ) -> tuple[int, list[ExtractedRequirement], bool]:
        async with semaphore:
            merged = await self._secondary_merge_single_pass(
                requirements=b_reqs,
                issues=b_issues,
                final_pass=False,
                allow_retry=True,
                pass_label=f"batch-{b_idx}",
            )
            fb = merged is None
            if merged is None:
                self._record_fallback(
                    "secondary_merge",
                    "secondary_batch_failed",
                    batch_idx=b_idx,
                    batch_count=total_batches,
                )
                li = b_issues or self._check_quality(b_reqs)
                merged = self._handle_local_issues(b_reqs, li)
            return b_idx, merged, fb

    tasks = [
        _process_secondary_batch(idx, reqs, iss)
        for idx, (reqs, iss) in enumerate(batches, start=1)
    ]
    for coro in asyncio.as_completed(tasks):
        batch_idx, merged, fallback_used = await coro
        results_map[batch_idx] = merged
        completed_count += 1
        logger.info(
            "二次合并批次完成: stage=secondary_merge batch={}/{} out={} fallback_used={}",
            batch_idx,
            total_batches,
            len(merged),
            fallback_used,
        )
        self._report_progress(
            "merging",
            85 + int(8 * completed_count / max(1, total_batches)),
            100,
            f"二次合并批次 {completed_count}/{total_batches}",
            substage="secondary_batch",
            meta={"batch_idx": batch_idx, "batch_count": total_batches},
        )

    merged_results: list[ExtractedRequirement] = []
    for idx in sorted(results_map):
        merged_results.extend(results_map[idx])
    if safe_prefix:
        merged_results = [*safe_prefix, *merged_results]

    final_issues = self._check_quality(merged_results)
    if (
        final_issues
        and len(merged_results) > max(2, settings.TENDER_SECONDARY_BATCH_SIZE)
    ):
        affected_set: set[int] = set()
        for issue in final_issues:
            for idx in issue.get("affected_indices", []):
                if isinstance(idx, int) and 0 <= idx < len(merged_results):
                    affected_set.add(idx)

        affected_reqs = [merged_results[i] for i in sorted(affected_set)]
        safe_reqs = [
            req for i, req in enumerate(merged_results)
            if i not in affected_set
        ]

        logger.info(
            "疑点收敛: stage=final_pass total={} affected={} safe={}",
            len(merged_results),
            len(affected_reqs),
            len(safe_reqs),
        )
        self._report_progress(
            "merging",
            94,
            100,
            f"疑点收敛（{len(affected_reqs)}条受影响）",
            substage="final_pass",
            meta={"mode": "targeted", "affected": len(affected_reqs), "safe": len(safe_reqs)},
        )

        final_merged_affected: list[ExtractedRequirement] = []
        if affected_reqs:
            idx_remap = {old: new for new, old in enumerate(sorted(affected_set))}
            remapped_issues: list[dict] = []
            for issue in final_issues:
                affected = issue.get("affected_indices", [])
                if not isinstance(affected, list):
                    continue
                local = [idx_remap[i] for i in affected if i in idx_remap]
                if len(local) < 2:
                    continue
                cloned = dict(issue)
                cloned["affected_indices"] = local
                remapped_issues.append(cloned)

            final_single_limit = max(1, settings.TENDER_FINAL_PASS_SINGLE_MAX_INPUT)
            if len(affected_reqs) <= final_single_limit:
                fp_result = await self._secondary_merge_single_pass(
                    requirements=affected_reqs,
                    issues=remapped_issues,
                    final_pass=True,
                    allow_retry=True,
                    pass_label="final-pass-targeted",
                )
                if fp_result is not None:
                    final_merged_affected = fp_result
                else:
                    self._record_fallback(
                        "final_pass", "final_targeted_failed",
                        input_count=len(affected_reqs),
                    )
                    final_merged_affected = self._handle_local_issues(
                        affected_reqs, remapped_issues
                    )
            else:
                fp_batches = self._build_secondary_batches(
                    affected_reqs, remapped_issues,
                    batch_size=settings.TENDER_FINAL_PASS_BATCH_SIZE,
                )
                total_fp = len(fp_batches)
                fp_concurrency = max(1, settings.TENDER_FINAL_PASS_CONCURRENCY)
                fp_sem = asyncio.Semaphore(fp_concurrency)
                fp_map: dict[int, list[ExtractedRequirement]] = {}

                async def _fp_batch(
                    bi: int, br: list[ExtractedRequirement], bi_iss: list[dict]
                ) -> tuple[int, list[ExtractedRequirement]]:
                    async with fp_sem:
                        fm = await self._secondary_merge_single_pass(
                            requirements=br, issues=bi_iss,
                            final_pass=True, allow_retry=True,
                            pass_label=f"final-pass-targeted-{bi}",
                        )
                        if fm is None:
                            li = bi_iss or self._check_quality(br)
                            fm = self._handle_local_issues(br, li)
                            self._record_fallback(
                                "final_pass", "final_targeted_batch_failed",
                                batch_idx=bi, batch_count=total_fp,
                            )
                        return bi, fm

                fp_tasks = [
                    _fp_batch(i, r, iss)
                    for i, (r, iss) in enumerate(fp_batches, start=1)
                ]
                for coro in asyncio.as_completed(fp_tasks):
                    bi, fm = await coro
                    fp_map[bi] = fm

                for bi in sorted(fp_map):
                    final_merged_affected.extend(fp_map[bi])

        result = safe_reqs + final_merged_affected

        post_issues = self._check_quality(result)
        if post_issues:
            logger.info(
                "疑点收敛后仍有 {} 个问题, 执行本地规则兜底",
                len(post_issues),
            )
            result = self._handle_local_issues(result, post_issues)

        self._report_progress(
            "merging", 96, 100,
            f"最终收敛完成（{len(result)}条）",
            substage="final_pass",
            meta={"output_count": len(result)},
        )
        return self._simple_dedupe(result)

    return self._simple_dedupe(merged_results)


def _collect_affected_indices(issues: list[dict], total: int) -> set[int]:
    affected_set: set[int] = set()
    for issue in issues:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list):
            continue
        for idx in affected:
            if isinstance(idx, int) and 0 <= idx < total:
                affected_set.add(idx)
    return affected_set


def _remap_issues(issues: list[dict], index_map: dict[int, int]) -> list[dict]:
    remapped: list[dict] = []
    for issue in issues:
        affected = issue.get("affected_indices", [])
        if not isinstance(affected, list):
            continue
        local = [index_map[idx] for idx in affected if idx in index_map]
        if len(local) < 2:
            continue
        cloned = dict(issue)
        cloned["affected_indices"] = local
        remapped.append(cloned)
    return remapped
