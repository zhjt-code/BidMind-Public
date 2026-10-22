"""评审意义判定器（P3 可评审性独立判定 pass，P4/ 证据载体轴 + 否决项双确认降级）。

职责: 对合并后的招标要求整表批量调用 LLM 判定 review_relevance（可评审性），
 合成规则地板/合同启发式/评分项豁免与否决项双确认降级，并识别含分值的评分项。
不做: 不提取/合并要求、不访问 DB、不渲染前端；仅在内存上修订 ExtractedRequirement。

合成优先级（从高到低）:
 1. 评分相关(LLM is_scoring 或正文分值正则命中) -> actionable + is_key_review(不赋 score_weight,
 满分唯一来源为评分表 scoring_item;)。评分项铁律: 任何情况不得折叠。
 2. 否决项(is_mandatory) -> 默认 actionable；仅当"LLM 证据载体判非投标文件 且
 任一规则地板确认(行为性词表/噪音规则/合同启发式,)"双确认成立时折叠
 (保留 is_mandatory=1, 前端进独立风险告知分组, 守 不可隐藏红线)。
 3. 折叠并集: LLM 判不可评审 或 规则库命中 或 合同启发式命中 -> informational/procedural
 4. 否则 actionable
失败回退: 单批 LLM 失败时,该批仅用规则地板 + 评分正则 + 否决豁免（否决项一律保持
actionable，不做无 LLM 的单翼降级；不阻断提取）。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any, Optional

from loguru import logger

from app.core.ai.prompts.relevance_classify import get_relevance_classify_prompt
from app.core.config import settings
from app.core.extractor.relevance.behavioral_disqualification import (
    has_reviewable_content_kernel,
    matches_behavioral_disqualification,
)
from app.core.extractor.section_context import classify_section_phase
from app.core.extractor.tools.noise_classifier import classify_noise_relevance
from app.schemas.requirement import ExtractedRequirement

_FOLD_LEVELS = ("informational", "procedural")

# 证据载体不在投标文件内容中的 locus 值（LLM 输出，见 relevance_classify prompt）
_NON_BID_LOCI = ("behavior", "procedure", "performance", "external", "notice")

# 含分值评分项的句子级识别: 分值数字必须与评分动作词直接搭配才算评分表达。
# 旧词级判定(文中任意位置出现"加/计/扣"任一字 + 任意位置"\d+分")曾把
# "使用…加密电子响应文件…20分钟内完成远程解密"双误判为 20 分评分项:
# "加密"命中语境词"加"、"20分钟"命中"20分", 两个不相干词级信号拼成评分项。
# 现约束: ①动作词(得/扣/加/计/评/满分…)紧邻数字分; ②排除"分钟/分贝/分之"等复合词。
_SCORE_UNIT_SUFFIX = r"(?![钟贝之秒米厘寸])"
_SCORE_BAND_RE = re.compile(
    r"(?:得|扣|加|计|评|赋|配|满分|基本分|基础分|标准分|最高|最多)\s*(\d+(?:\.\d+)?)\s*分"
    + _SCORE_UNIT_SUFFIX
    + r"|(\d+(?:\.\d+)?)\s*分"
    + _SCORE_UNIT_SUFFIX
    + r"\s*(?:制|封顶|为满分|为上限)"
)


def _parse_inline_score(description: str | None) -> tuple[bool, Optional[float]]:
    """句子级识别正文是否为含分值评分项,并估算满分(取最大分档)。"""
    text = description or ""
    nums: list[float] = []
    for match in _SCORE_BAND_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            nums.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not nums:
        return False, None
    max_score = max(nums)
    # 满分异常大(如金额误命中)时不视为评分项
    if max_score <= 0 or max_score > 100:
        return False, None
    return True, max_score


def _item_payload(idx: int, item: ExtractedRequirement) -> dict[str, Any]:
    desc = item.description or ""
    return {
        "idx": idx,
        "category": item.category,
        "is_mandatory": bool(item.is_mandatory),
        "description": desc[:300],
    }


def _mandatory_rule_floor(item: ExtractedRequirement) -> Optional[tuple[str, str]]:
    """否决项双确认的规则翼：任一确定性规则地板命中即返回 (折叠级别, 归因)。: is_mandatory 来自无上下文分块的一次 LLM 判断，规则翼若只认行为性
 词表，程序性（投标意向确认书邮件发送）、告知性（招标人出资比例）等同形态
 否决误标都无法折叠。这里把规则翼泛化为全部规则地板，与非否决项共用同一套；
 内容性例外（可由投标文件核验的内核）对全部地板统一守门，不只守行为性词表。
 """
    if has_reviewable_content_kernel(item.description):
        return None
    if matches_behavioral_disqualification(item.description):
        return "procedural", "behavioral_mandatory"
    rule_level = classify_noise_relevance(item.description, item.keywords, item.category)
    if rule_level in _FOLD_LEVELS:
        return rule_level, "rule_floor"
    if classify_section_phase(None, item.description).is_contract:
        return "informational", "contract_section"
    return None


def _mandatory_double_confirmed_fold(
    item: ExtractedRequirement, llm_res: Optional[dict]
) -> Optional[tuple[str, str]]:
    """否决项双确认降级判定: LLM 证据载体 + 规则地板同时确认才允许折叠。

 双确认设计（红线: 否决项不可轻易折叠隐藏）:
 - LLM 翼: locus 判为非投标文件载体; 兼容旧版 Prompt 无 locus 时，以
 relevance 判折叠作为 LLM 翼（LLM 已认定不可评审）。
 - 规则翼: 任一规则地板命中（_mandatory_rule_floor）。
 - LLM 失败（llm_res 为 None）时恒不折叠（保守回退）。
 返回 (review_relevance, relevance_reason)；不折叠返回 None。
 """
    if not settings.TENDER_BEHAVIORAL_MANDATORY_FOLD_ENABLED:
        return None
    if not llm_res:
        return None
    locus = llm_res.get("locus")
    if locus is not None:
        llm_wing = locus in _NON_BID_LOCI
    else:
        llm_wing = llm_res.get("relevance") in _FOLD_LEVELS
    if not llm_wing:
        return None
    return _mandatory_rule_floor(item)


def _decide_updates(item: ExtractedRequirement, llm_res: Optional[dict]) -> dict[str, Any]:
    """对单条要求合成最终评审意义与评分项标记。"""
    # 1. 评分相关识别(LLM 或正文含分值语境): 仅标记重点, 不赋 score_weight。
    # 评分项满分的唯一权威来源 = 评分表结构化提取(scoring_item, 满分取"满分分值"列)。
    #    正文不再"取最大数字当满分"(此前 _parse_inline_score 把"价格总分100分"标成 score_weight=100,
    #    经 is_key_review 在确认页冒出误导性"评分项100分")。is_key_review 仅保留"评分相关/重点"语义。
    # 评分项铁律: 评分相关优先级最高, 即使否决项/折叠候选也先落此分支, 永不折叠。
    llm_is_scoring = bool(llm_res.get("is_scoring")) if llm_res else False
    regex_scoring, _ = _parse_inline_score(item.description)
    if llm_is_scoring or regex_scoring:
        return {
            "review_relevance": "actionable",
            "is_key_review": True,
            "scoring_metadata": {"source": "inline"},
        }

    # 2. 否决项: 默认豁免; 仅双确认(LLM 载体 + 任一规则地板)成立时折叠为风险告知
    if item.is_mandatory:
        folded = _mandatory_double_confirmed_fold(item, llm_res)
        if folded:
            level, reason = folded
            return {"review_relevance": level, "relevance_reason": reason}
        return {"review_relevance": "actionable"}

    # 3. 折叠并集: LLM 判定 -> 规则地板 -> 合同启发式（附归因, P2.1）
    llm_rel = (llm_res or {}).get("relevance")
    if llm_rel in _FOLD_LEVELS:
        return {"review_relevance": llm_rel, "relevance_reason": "llm_locus"}
    rule_level = classify_noise_relevance(item.description, item.keywords, item.category)
    if rule_level:
        return {"review_relevance": rule_level, "relevance_reason": "rule_floor"}
    if classify_section_phase(None, item.description).is_contract:
        return {"review_relevance": "informational", "relevance_reason": "contract_section"}

    # 4. 默认可评审
    return {"review_relevance": "actionable"}


def _normalize_llm_array(response: Any) -> list[dict]:
    if isinstance(response, list):
        return [r for r in response if isinstance(r, dict)]
    if isinstance(response, dict):
        for key in ("items", "results", "data"):
            if isinstance(response.get(key), list):
                return [r for r in response[key] if isinstance(r, dict)]
    return []


async def _classify_batch(
    llm_client: Any,
    batch: list[ExtractedRequirement],
) -> dict[int, dict]:
    """对单批要求调用 LLM,返回 batch-local idx -> 判定结果。失败返回空(交回退)。"""
    payload = [_item_payload(i, item) for i, item in enumerate(batch)]
    requirements_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    system_msg, user_msg = get_relevance_classify_prompt(requirements_json)
    try:
        response = await llm_client.chat_json(
            user_message=user_msg,
            system_message=system_msg,
            temperature=0.0,
            max_tokens=max(1500, len(batch) * 50),
        )
    except Exception as e:
        logger.warning("评审意义判定 LLM 失败(回退规则): batch_size={} error={}", len(batch), e)
        return {}

    result_map: dict[int, dict] = {}
    for entry in _normalize_llm_array(response):
        idx = entry.get("idx")
        if isinstance(idx, int) and 0 <= idx < len(batch):
            result_map[idx] = entry
    return result_map


async def classify_review_relevance(
    items: list[ExtractedRequirement],
    llm_client: Any,
    *,
    batch_size: Optional[int] = None,
    concurrency: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> list[ExtractedRequirement]:
    """对整表要求批量判定评审意义,返回修订后的列表(顺序不变)。"""
    if not items:
        return items

    size = batch_size or max(5, settings.TENDER_RELEVANCE_BATCH_SIZE)
    batches = [items[i:i + size] for i in range(0, len(items), size)]

    effective_concurrency = (
        concurrency if concurrency is not None
        else max(1, settings.TENDER_RELEVANCE_CONCURRENCY)
    )
    semaphore = asyncio.Semaphore(max(1, effective_concurrency))

    async def _run(batch_index: int, batch: list[ExtractedRequirement]):
        async with semaphore:
            return batch_index, await _classify_batch(llm_client, batch)

    result_by_batch: dict[int, dict] = {}
    completed = 0
    total_batches = len(batches)
    for coro in asyncio.as_completed(
        [_run(bi, batch) for bi, batch in enumerate(batches)]
    ):
        bi, rmap = await coro
        result_by_batch[bi] = rmap
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, total_batches)

    refined: list[ExtractedRequirement] = []
    folded = 0
    scoring = 0
    for bi, batch in enumerate(batches):
        rmap = result_by_batch.get(bi, {})
        for local_idx, item in enumerate(batch):
            updates = _decide_updates(item, rmap.get(local_idx))
            if updates.get("review_relevance") in _FOLD_LEVELS:
                folded += 1
            if updates.get("is_key_review"):
                scoring += 1
            refined.append(item.model_copy(update=updates))

    logger.info(
        "评审意义判定完成: 共{}条, 折叠{}条, 含分值评分项{}条",
        len(refined), folded, scoring,
    )
    return refined
