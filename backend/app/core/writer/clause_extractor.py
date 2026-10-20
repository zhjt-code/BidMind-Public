"""三层编写约束提取器

从招标文件分块中并行提取三层约束(硬约束/结构约束/生成引导)，
同层去重后写入 writing_clauses 表。

架构参照 tender_extractor.py:
  分块并行提取 -> 合并去重 -> 保存到 DB
"""

import asyncio
import json
import re
import time
from typing import Any, Callable, Optional

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.ai.json_response import require_dict_response
from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.config import get_review_concurrency, settings
from app.core.parser.text_normalizer import compat_ratio as _compat_ratio
from app.core.writer.clause_writability_filter import split_writable_clauses
from app.core.writer.fixed_template import (
    FixedTemplateEntry,
    build_fixed_template_catalog_async,
    match_template_title,
)
from app.core.writer.fixed_template.verbatim_extractor import (
    enrich_snapshots_verbatim,
)
from app.core.writer.fixed_template_contract import (
    FIXED_TEMPLATE_SNAPSHOT_KEY,
    FixedTemplateSnapshot,
)
from app.core.writer.prompts.clause_extract import (
    get_bid_structure_analysis_prompt,
    get_dedup_decision_prompt,
    get_render_profile_prompt,
)
from app.core.writer.prompts.clause_extract import (
    get_generation_guide_prompt as _code_generation_guide,
)
from app.core.writer.prompts.clause_extract import (
    get_hard_constraint_prompt as _code_hard_constraint,
)
from app.core.writer.prompts.clause_extract import (
    get_structure_constraint_prompt as _code_structure_constraint,
)
from app.core.writer.text_budget import make_priority_shrink_reducer
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.models.writing_render_profile import WritingRenderProfile
from app.schemas.writing import (
    BidStructureAnalysis,
    ClauseMergeDecision,
    ExtractedClause,
    RenderProfileParsed,
)

ProgressCallback = Callable[..., None]


def _registry_prompt_wrapper(node_key: str, code_fn):
    """为 _LAYER_PROMPT_MAP 创建 Registry 优先的包装函数

    签名约定: wrapped(content, page_start, page_end) -> (system_msg, user_msg)
    命中 Registry 时对 user_template 做变量注入；注入失败则回退代码默认 Prompt。
    """
    def wrapped(*args, **kwargs):
        from app.core.ai.prompt_registry import get_registry
        resolved = get_registry().get_prompt(node_key)
        if resolved.system_message and resolved.user_template:
            fmt_vars = {}
            if len(args) >= 1:
                fmt_vars["content"] = args[0]
            if len(args) >= 2:
                fmt_vars["page_start"] = args[1]
            if len(args) >= 3:
                fmt_vars["page_end"] = args[2]
            fmt_vars.update(kwargs)
            try:
                formatted_user = resolved.user_template.format(**fmt_vars)
                logger.debug(
                    f"PromptRegistry hit: node={node_key}, "
                    f"formatted_len={len(formatted_user)}"
                )
                return resolved.system_message, formatted_user
            except (KeyError, IndexError, ValueError) as e:
                warning_key = (
                    node_key,
                    resolved.system_version_id,
                    resolved.user_version_id,
                    repr(e),
                )
                log_message = (
                    "PromptRegistry user_template 格式化失败, "
                    f"node={node_key}, system_v={resolved.system_version_id}, "
                    f"user_v={resolved.user_version_id}, error={e}, 回退代码默认 Prompt"
                )
                if warning_key not in _PROMPT_FORMAT_WARNING_KEYS:
                    _PROMPT_FORMAT_WARNING_KEYS.add(warning_key)
                    logger.warning(log_message)
                else:
                    logger.debug(log_message)
        return code_fn(*args, **kwargs)
    return wrapped


_LAYER_PROMPT_MAP = {
    "hard_constraint": _registry_prompt_wrapper("writing.hard_clause", _code_hard_constraint),
    "structure_constraint": _registry_prompt_wrapper("writing.structure_clause", _code_structure_constraint),
    "generation_guide": _registry_prompt_wrapper("writing.guide_clause", _code_generation_guide),
}
_LAYER_NODE_KEY_MAP = {
    "hard_constraint": "writing.hard_clause",
    "structure_constraint": "writing.structure_clause",
    "generation_guide": "writing.guide_clause",
}
_PROMPT_FORMAT_WARNING_KEYS: set[tuple[str, Optional[int], Optional[int], str]] = set()

# 每次送入 LLM 的分块数量上限
_CHUNKS_PER_BATCH = 3

# 三层事实提取输出预算需与 LLM Center seed 默认值保持一致。
_EXTRACT_BATCH_MAX_TOKENS = 4000

# CJK Compat 字符占比阈值，超过则拒绝 LLM 调用
_COMPAT_RATIO_THRESHOLD = 0.02
_FIXED_TEMPLATE_SNAPSHOT_MAX_CHARS = 12000


def _fixed_template_lookup_text(ext: ExtractedClause) -> str:
    """构造 fixed_template catalog 匹配文本。"""
    metadata = ext.metadata or {}
    parts = [
        str(metadata.get("target_section_hint") or ""),
        str(metadata.get("applies_to") or ""),
        ext.content or "",
        ext.raw_text or "",
    ]
    return " ".join(part for part in parts if part)


def _match_catalog_entry(
    ext: ExtractedClause,
    catalog: list[FixedTemplateEntry],
) -> Optional[FixedTemplateEntry]:
    """为 LLM 提取的 fixed_template 约束匹配具体模板条目。: 同一 template_key 可能存在技术标/商务标多卷条目(标题相同),
 仅按标题取首个会把约束错配到另一卷。命中多个候选时优先取页锚点
 落在条目页范围内的那条; 无页锚点或不落任何范围时回退首个候选。
 """
    lookup = _fixed_template_lookup_text(ext)
    candidates = [
        entry for entry in catalog
        if match_template_title(lookup, entry.title, entry.template_key).matched
    ]
    if not candidates:
        return None
    ext_page = ext.source_page if ext.source_page is not None else ext.source_page_start
    if ext_page is not None:
        for entry in candidates:
            start = entry.source_page_start
            end = entry.source_page_end if entry.source_page_end is not None else start
            if start is not None and end is not None and start <= ext_page <= end:
                return entry
    return candidates[0]


def _content_batch_overlap(content: str, batch_lower: str) -> float:
    """计算 clause content 与 batch 原文的关键词交集比例。

    提取 content 中 >= 2 字的中文词/短语，计算其在 batch 中出现的比例。
    用于识别 LLM 幻觉：当 LLM 编造了与输入无关的 content 时，overlap 接近 0。
    """
    keywords = re.findall(r"[\u4e00-\u9fff]{2,4}", content or "")
    if not keywords:
        return 1.0
    unique_kw = set(keywords)
    if not unique_kw:
        return 1.0
    matched = sum(1 for kw in unique_kw if kw in batch_lower)
    return matched / len(unique_kw)


def _check_chunk_text_quality(text: str, flow: str) -> float:
    """检测文本的 CJK compat 字符占比并记录指标。"""
    ratio = _compat_ratio(text)
    try:
        from app.core.observability.metrics import chunk_text_quality_total
        outcome = "degraded" if ratio > _COMPAT_RATIO_THRESHOLD else "clean"
        chunk_text_quality_total.labels(flow=flow, outcome=outcome).inc()
    except Exception:
        pass
    return ratio


def _metadata_brief(metadata: Optional[dict[str, Any]]) -> str:
    """将约束结构化元数据压缩为下游 Prompt 可消费的短文本。"""
    if not metadata:
        return ""
    useful_keys = [
        "score_item_name", "max_score", "score_rules", "deduction_rules",
        "target_section_hint", "evidence_hint", "material_kind",
        "required_form", "applies_to", "is_mandatory", "stage",
        "risk_consequence",
    ]
    compact = {
        key: metadata.get(key)
        for key in useful_keys
        if metadata.get(key) not in (None, "", [], {})
    }
    if not compact:
        return ""
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


class ClauseExtractor:
    """三层编写约束提取器

    从招标文件的分块中提取 hard_constraint / structure_constraint / generation_guide
    三层约束，同层去重后写入 writing_clauses 表。
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        concurrency: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self.concurrency = (
            concurrency if concurrency is not None else get_review_concurrency()
        )
        self.progress_callback = progress_callback

    # ==================== 主入口 ====================

    async def extract(
        self,
        project_id: int,
        document_id: int,
    ) -> list[WritingClause]:
        """执行三层约束提取

        Args:
            project_id: 项目 ID
            document_id: 招标文件 document ID

        Returns:
            去重后写入 DB 的约束列表
        """
        chunks = await self._load_chunks(document_id)
        if not chunks:
            logger.warning(f"文档 {document_id} 无分块，跳过约束提取")
            return []

        batches = self._build_batches(chunks)
        total_batches = len(batches)
        logger.info(
            f"约束提取启动: project={project_id}, doc={document_id}, "
            f"chunks={len(chunks)}, batches={total_batches}"
        )

        from app.core.writer.clause_layer_pass import (
            ClauseLayerPassRequest,
            extract_clause_layers_from_batches,
        )

        t0 = time.time()
        layer_result = await extract_clause_layers_from_batches(
            ClauseLayerPassRequest(
                batches=batches,
                all_chunks=chunks,
                llm_client=self.llm_client,
                concurrency=self.concurrency,
                report_progress=self._report_progress,
                verify_batch_clauses=self._verify_batch_clauses,
                detect_section_context=self._detect_section_context,
                check_text_quality=_check_chunk_text_quality,
            )
        )
        hard, structure, guide = (
            layer_result.hard,
            layer_result.structure,
            layer_result.guide,
        )
        extract_elapsed = time.time() - t0
        logger.info(
            f"三层提取完成: hard={len(hard)}, structure={len(structure)}, "
            f"guide={len(guide)}, 耗时={extract_elapsed:.1f}s"
        )

        # 写作可操作性兜底过滤: 剔除 hard 层中 Prompt 未拦住的纯行为性废标条款,
        # 前移"删除后不影响正文编写"的判定, 避免噪音进入去重/确认/匹配全链路。
        if settings.WRITING_WRITABILITY_FILTER_ENABLED:
            hard, hard_dropped = split_writable_clauses(hard)
            if hard_dropped:
                logger.info(
                    f"写作可操作性过滤: hard 层剔除 {len(hard_dropped)} 条行为性废标条款"
                )

        self._report_progress("dedup", 0, 3, "三层并行去重中")
        t_dedup = time.time()
        hard_deduped, structure_deduped, guide_deduped = await asyncio.gather(
            self._deduplicate(hard),
            self._deduplicate(structure),
            self._deduplicate(guide),
        )
        dedup_elapsed = time.time() - t_dedup
        logger.info(
            f"去重: hard {len(hard)}->{len(hard_deduped)} (合并{len(hard)-len(hard_deduped)}条), "
            f"structure {len(structure)}->{len(structure_deduped)} (合并{len(structure)-len(structure_deduped)}条), "
            f"guide {len(guide)}->{len(guide_deduped)} (合并{len(guide)-len(guide_deduped)}条), "
            f"耗时={dedup_elapsed:.1f}s"
        )
        self._report_progress("dedup", 3, 3, "去重完成")

        hard_deduped, structure_deduped = self._cross_layer_dedup_material_notice(
            hard_deduped, structure_deduped
        )

        all_clauses = hard_deduped + structure_deduped + guide_deduped

        reclassified = self._reclassify_fixed_templates(all_clauses)
        if reclassified > 0:
            logger.info(f"规则兜底: {reclassified} 条约束重分类为 fixed_template")
        logger.info(f"约束提取汇总: total={len(all_clauses)}")

        # 写入 DB。完成态由 writing_extract_task 在评分/画像全部结束后再报，
        # 此处禁止报 completed，否则上传页会把评分项/画像闪成满条完成。
        saved = await self._save_clauses(project_id, document_id, all_clauses)
        return saved

    # ==================== 渲染配置解析 ====================

    async def parse_render_profile(
        self,
        project_id: int,
        format_rule_clauses: list[WritingClause],
    ) -> Optional[WritingRenderProfile]:
        """从 format_rule 类约束中解析渲染配置

        Args:
            project_id: 项目 ID
            format_rule_clauses: format_rule 类型的约束列表

        Returns:
            WritingRenderProfile 或 None(无格式规定时)
        """
        if not format_rule_clauses:
            return None

        format_text = "\n\n".join(
            c.content + (f"\n原文: {c.raw_text}" if c.raw_text else "")
            for c in format_rule_clauses
        )
        system_msg, user_msg = get_render_profile_prompt(format_text)
        temperature, max_tokens = resolve_runtime_params(
            "writing.render_config", 0.1, 1000,
        )

        try:
            result = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            result = require_dict_response(
                result, operation_name=f"渲染配置解析(project={project_id})"
            )
            parsed = RenderProfileParsed(**result)
        except Exception as e:
            logger.warning(f"渲染配置解析失败，使用默认值: {e}")
            parsed = RenderProfileParsed()

        from app.core.writer.bid_format_defaults import normalize_line_spacing
        raw_ls = parsed.line_spacing
        parsed.line_spacing = normalize_line_spacing(raw_ls)
        if raw_ls is not None and parsed.line_spacing != raw_ls:
            logger.warning(
                "line_spacing 归一化: raw={}, normalized={}, project={}",
                raw_ls, parsed.line_spacing, project_id,
            )

        existing = (await self.db.execute(
            select(WritingRenderProfile).where(
                WritingRenderProfile.project_id == project_id
            )
        )).scalars().first()

        clause_ids = [c.id for c in format_rule_clauses if c.id]
        source_clause_ids_json = json.dumps(clause_ids) if clause_ids else None

        if existing:
            for field_name in RenderProfileParsed.model_fields:
                value = getattr(parsed, field_name)
                if value is not None:
                    setattr(existing, field_name, value)
            existing.source_clause_ids = source_clause_ids_json
            profile = existing
        else:
            profile = WritingRenderProfile(project_id=project_id)
            for field_name in RenderProfileParsed.model_fields:
                value = getattr(parsed, field_name)
                if value is not None:
                    setattr(profile, field_name, value)
            profile.source_clause_ids = source_clause_ids_json
            self.db.add(profile)

        await self.db.commit()
        await self.db.refresh(profile)
        action = "updated" if existing else "created"
        logger.info(f"渲染配置已保存({action}): project={project_id}, id={profile.id}")
        return profile

    # ==================== 标书结构分析 ====================

    async def analyze_bid_structure(
        self,
        project_id: int,
        document_id: int,
        clauses: list[WritingClause],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> WritingClause:
        """分析招标文件要求的投标文件分卷结构

        基于已提取的约束（directory_rule / scoring_criterion / content_required
        / material_required）和行业画像，调用 LLM 判定分卷类型。
        结果以 JSON 存入 writing_clauses (sub_type=bid_structure_analysis, confirmed=1)。

        Args:
            project_id: 项目 ID
            document_id: 招标文件 document ID
            clauses: 已提取并保存的全部约束

        Returns:
            存储分析结果的 WritingClause 记录
        """
        def _filter(sub: str) -> str:
            items = [c for c in clauses if c.sub_type == sub]
            if not items:
                return ""
            return "\n".join(
                f"- {c.content}"
                + (f" ({c.score_weight}分)" if c.score_weight else "")
                + (
                    f" | metadata={_metadata_brief(c.metadata_json)}"
                    if _metadata_brief(getattr(c, "metadata_json", None))
                    else ""
                )
                for c in items
            )

        profile_clause = next(
            (c for c in clauses if c.sub_type == "industry_profile"), None
        )

        if progress_callback:
            progress_callback("整理结构分析输入")

        context = {
            "directory_rules": _filter("directory_rule"),
            "scoring_criteria": _filter("scoring_criterion"),
            "content_requirements": _filter("content_required"),
            "material_requirements": _filter("material_required"),
            "hard_structure_signals": "\n".join(
                part for part in [
                    _filter("fixed_template"),
                    _filter("submission_rule"),
                    _filter("submission_notice"),
                    _filter("format_rule"),
                ]
                if part
            ),
            "industry_profile": profile_clause.content if profile_clause else "",
        }

        max_attempts = 3
        last_error: Optional[Exception] = None
        analysis: Optional[BidStructureAnalysis] = None

        for attempt in range(max_attempts):
            try:
                if progress_callback:
                    progress_callback(f"等待结构分析模型返回（第{attempt + 1}次）")

                async def _send_request(payload: dict[str, str]):
                    system_msg, user_msg = get_bid_structure_analysis_prompt(**payload)
                    base_temperature, max_tokens = resolve_runtime_params(
                        "writing.bid_structure_analysis", 0.1, 2000,
                    )
                    # S.3.3: 统一重试策略为"首次 0.1，重试降至 0.0"（与 L961 去重决策保持一致）
                    # 旧逻辑 `0.1 + attempt * 0.1` 在重试时反而升温，与事实性提取目标冲突
                    return await self.llm_client.chat_json(
                        user_message=user_msg,
                        system_message=system_msg,
                        temperature=base_temperature if attempt == 0 else 0.0,
                        max_tokens=max_tokens,
                    )

                _reduce_payload = make_priority_shrink_reducer(
                    [
                        ("material_requirements", 1),
                        ("content_requirements", 1),
                        ("scoring_criteria", 1),
                        ("hard_structure_signals", 1),
                        ("industry_profile", 2),
                    ],
                    kept_segments=["directory_rules", "bid_structure_contract"],
                )

                raw = await retry_on_input_too_long_400(
                    operation_name=f"标书结构分析(project={project_id})",
                    initial_payload=context,
                    send_request=_send_request,
                    reduce_payload_semantically=_reduce_payload,
                    max_retries=1,
                    describe_payload=lambda payload: {
                        key: len(value)
                        for key, value in payload.items()
                        if isinstance(value, str)
                    },
                )
                analysis = BidStructureAnalysis(**(raw if isinstance(raw, dict) else {}))
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    f"标书结构分析LLM调用失败(第{attempt+1}/{max_attempts}次): {e}"
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        if analysis is None:
            logger.error(f"标书结构分析{max_attempts}次尝试均失败，使用默认值(tech_comm)")
            analysis = BidStructureAnalysis(
                structure_mode="tech_comm",
                has_technical=True,
                has_commercial=True,
                reasoning=f"LLM分析{max_attempts}次失败: {str(last_error)[:100]}",
            )

        content_json = analysis.model_dump_json(indent=2)

        clause = WritingClause(
            project_id=project_id,
            document_id=document_id,
            layer=WritingClauseLayer.structure_constraint,
            sub_type="bid_structure_analysis",
            content=content_json,
            raw_text=None,
            source_page=None,
            score_weight=None,
            confirmed=1,
            sort_order=9998,
        )
        if progress_callback:
            progress_callback("保存结构分析结果")
        self.db.add(clause)
        await self.db.commit()
        await self.db.refresh(clause)

        logger.info(
            f"标书结构分析完成: project={project_id}, "
            f"mode={analysis.structure_mode}, reasoning={analysis.reasoning[:80]}"
        )
        return clause

    # ==================== 目录原文结构化提取 ====================

    async def extract_bid_directory(
        self,
        project_id: int,
        document_id: int,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> WritingClause:
        """从招标文件中提取明确的投标文件目录结构。

        兼容委托: 实际编排见 bid_directory_extractor.extract_bid_directory_spec。
        """
        from app.core.writer.bid_directory_extractor import extract_bid_directory_spec

        return await extract_bid_directory_spec(
            db=self.db,
            llm_client=self.llm_client,
            project_id=project_id,
            document_id=document_id,
            progress_callback=progress_callback,
        )

    # ==================== 分块与批次 ====================

    async def _load_chunks(self, document_id: int) -> list[Chunk]:
        """加载文档的全部分块"""
        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
        )
        return list(result.scalars().all())

    def _build_batches(self, chunks: list[Chunk]) -> list[list[Chunk]]:
        """将分块按 _CHUNKS_PER_BATCH 分组"""
        return [
            chunks[i:i + _CHUNKS_PER_BATCH]
            for i in range(0, len(chunks), _CHUNKS_PER_BATCH)
        ]

    # ==================== 规则兜底重分类 ====================

    _FT_CONTENT_KEYWORDS = (
        "固定模板", "按指定格式", "使用招标文件提供的格式",
        "按照以下格式", "使用指定格式",
    )
    _FT_TITLE_KEYWORDS = (
        "承诺书", "声明函", "声明", "确认书", "回执",
        "授权书", "投标函", "投标函附录",
        "附录", "一览表", "开标一览表", "报价书", "报价表",
    )

    def _reclassify_fixed_templates(
        self, clauses: list[ExtractedClause],
    ) -> int:
        """扫描非 fixed_template 约束,按关键词规则将遗漏项重分类"""
        count = 0
        for c in clauses:
            if c.sub_type == "fixed_template":
                continue
            text = (c.content or "") + (c.raw_text or "")
            has_format_kw = any(kw in text for kw in self._FT_CONTENT_KEYWORDS)
            has_title_kw = any(kw in text for kw in self._FT_TITLE_KEYWORDS)
            if has_format_kw and has_title_kw:
                logger.debug(
                    f"兜底重分类: '{c.content[:40]}' "
                    f"{c.sub_type} -> fixed_template"
                )
                c.sub_type = "fixed_template"
                count += 1
        return count

    # ==================== 单层提取 ====================

    async def _extract_layer(
        self,
        layer: str,
        batches: list[list[Chunk]],
        total_batches: int,
        all_chunks: Optional[list[Chunk]] = None,
    ) -> list[ExtractedClause]:
        """对所有批次执行单层提取"""
        prompt_fn = _LAYER_PROMPT_MAP[layer]
        semaphore = asyncio.Semaphore(self.concurrency)
        extracted_count = 0
        all_clauses: list[ExtractedClause] = []

        async def extract_batch(batch: list[Chunk]) -> list[ExtractedClause]:
            nonlocal extracted_count
            async with semaphore:
                try:
                    result = await self._extract_batch(batch, prompt_fn, layer, all_chunks)
                    extracted_count += 1
                    stage = f"extracting_{layer}"
                    self._report_progress(
                        stage, extracted_count, total_batches,
                        f"{layer}: {extracted_count}/{total_batches}"
                    )
                    return result
                except (KeyError, IndexError) as e:
                    extracted_count += 1
                    logger.error(
                        f"{layer} Prompt 格式化失败，终止约束提取任务: {e}"
                    )
                    raise
                except Exception as e:
                    extracted_count += 1
                    logger.warning(f"{layer} 批次提取失败: {e}")
                    # P0-D: 批次静默丢弃会造成约束缺失——记录降级信号
                    # (兜底行为本身不变, 但失败必须可见, 供任务警告呈现)
                    try:
                        from app.core.contracts.degradation_signal import (
                            DegradationSignal,
                            push_signal,
                        )
                        push_signal(DegradationSignal(
                            source="clause_batch_extract_failed",
                            flow="writer",
                            repair_type="batch_dropped",
                            original_length=sum(len(c.content or "") for c in batch),
                            recovered_elements=0,
                        ))
                    except Exception:
                        pass
                    return []

        tasks = [extract_batch(batch) for batch in batches]
        results = await asyncio.gather(*tasks)
        for batch_result in results:
            all_clauses.extend(batch_result)
        return all_clauses

    async def _extract_batch(
        self,
        batch: list[Chunk],
        prompt_fn: Callable[..., tuple[str, str]],
        layer: str,
        all_chunks: Optional[list[Chunk]] = None,
    ) -> list[ExtractedClause]:
        """对单个批次调用 LLM 提取约束"""
        content_parts = []
        page_nums = []

        if all_chunks and batch:
            # 章节上下文仅用于增强 Prompt 提示，属锦上添花。
            # 其失败绝不能终止整个约束提取任务。
            try:
                section, is_contract = self._detect_section_context(
                    batch[0], all_chunks
                )
            except Exception as e:
                logger.warning(f"章节上下文检测失败，降级为空 section: {e}")
                section, is_contract = "", False
            if section:
                phase = "合同/履约阶段" if is_contract else "投标准备阶段"
                content_parts.append(
                    f"[当前内容所属章节: {section}（{phase}）]"
                )

        for chunk in batch:
            content_parts.append(chunk.content or "")
            if chunk.page_num is not None:
                page_nums.append(chunk.page_num)

        content = "\n\n".join(content_parts)
        page_start = min(page_nums) if page_nums else 0
        page_end = max(page_nums) if page_nums else 0

        ratio = _check_chunk_text_quality(content, "writer")
        if ratio > _COMPAT_RATIO_THRESHOLD:
            logger.warning(
                f"Chunk 文本质量异常（CJK compat ratio={ratio:.3f} > {_COMPAT_RATIO_THRESHOLD}），"
                f"pages={page_start}-{page_end}，跳过 LLM 调用"
            )
            return []

        system_msg, user_msg = prompt_fn(content, page_start, page_end)
        node_key = _LAYER_NODE_KEY_MAP.get(layer, "writing.hard_clause")
        temperature, max_tokens = resolve_runtime_params(
            node_key, 0.1, _EXTRACT_BATCH_MAX_TOKENS,
        )

        result = await self.llm_client.chat_json(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        raw_list = result if isinstance(result, list) else result.get("clauses", [])
        clauses = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                clause = ExtractedClause(**item)
                clauses.append(clause)
            except Exception as e:
                logger.warning(f"单条约束解析失败: item={item}, err={e}")

        # ── 证据闸门: source_page 范围校验 + raw_text 来源校验 (推广) ──
        clauses = self._verify_batch_clauses(clauses, content, page_start, page_end)
        return clauses

    @staticmethod
    def _verify_batch_clauses(
        clauses: list[ExtractedClause],
        batch_text: str,
        page_start: int,
        page_end: int,
    ) -> list[ExtractedClause]:
        """对单批提取结果做证据闸门校验

 强化：当 raw_text 与 batch 不匹配时，同步校验 content
 是否与 batch 有关键词交集。若整批 clause 均为幻觉则整批丢弃。

 本方法同时维护批次级证据统计，用于区分:
 - llm_missing: LLM 本就未给 raw_text
 - gate_cleared: LLM 给出伪原文被闸门清除
 便于后续排查 raw_text 缺失的真实来源。
 """
        from difflib import SequenceMatcher

        batch_lower = (batch_text or "").lower()
        verified: list[ExtractedClause] = []
        llm_missing_count = 0
        gate_cleared_count = 0

        for clause in clauses:
            if clause.source_page is not None:
                if page_start > 0 and page_end > 0:
                    if clause.source_page < page_start or clause.source_page > page_end:
                        # B4: 修复 同源问题 — 超出批级范围的 source_page
                        # 不再"修正为 page_start"（伪精确单页），而是清空，由
                        # source_page_start/end 承担用户态范围语义。
                        logger.info(
                            f"[EvidenceGate] 约束source_page={clause.source_page}超出批级范围"
                            f"[{page_start}-{page_end}], 清空为 None（避免伪精确单页跳转）"
                        )
                        clause.source_page = None
            elif page_start > 0:
                clause.source_page = page_start

            # 保留批次页范围作为面向用户的主语义 (诚实范围);
            # source_page 此后仅用于 UI 默认打开 PDF 的锚点, 不承担精确展示职责。
            if page_start > 0 and page_end > 0:
                clause.source_page_start = page_start
                clause.source_page_end = page_end

            raw_ok = True
            if not clause.raw_text:
                llm_missing_count += 1
            if clause.raw_text and batch_text:
                raw = clause.raw_text.strip()
                if len(raw) > 10:
                    if raw in batch_text:
                        pass
                    else:
                        ratio = SequenceMatcher(
                            None, raw[:200], batch_text
                        ).ratio()
                        if ratio < 0.3:
                            logger.info(
                                f"[EvidenceGate] 约束raw_text与批次原文不匹配(ratio={ratio:.2f}), "
                                f"清除伪原文: '{raw[:80]}...'"
                            )
                            clause.raw_text = None
                            raw_ok = False
                            gate_cleared_count += 1
                            # B4: raw_text 被识别为幻觉时，source_page 同步清空，
                            # 避免"原文"按钮跳到一个根本无引用的页码（a 同源治理）
                            clause.source_page = None

            if not raw_ok and batch_text:
                content_ratio = _content_batch_overlap(clause.content, batch_lower)
                if content_ratio < 0.05:
                    logger.warning(
                        f"[EvidenceGate] 约束content与批次原文无关(overlap={content_ratio:.3f}), "
                        f"丢弃幻觉clause: '{(clause.content or '')[:80]}...'"
                    )
                    continue

            verified.append(clause)

        if clauses and not verified:
            logger.warning(
                f"[EvidenceGate] 批次[{page_start}-{page_end}]全部{len(clauses)}条clause"
                f"被判定为幻觉，整批丢弃"
            )

        if clauses and (llm_missing_count or gate_cleared_count):
            logger.info(
                f"[EvidenceGate] 批次[{page_start}-{page_end}] 证据统计: "
                f"total={len(clauses)}, verified={len(verified)}, "
                f"llm_missing_raw_text={llm_missing_count}, "
                f"gate_cleared_raw_text={gate_cleared_count}"
            )

        return verified

    # ==================== 去重 ====================

    @staticmethod
    def _local_pre_dedupe(clauses: list[ExtractedClause]) -> list[ExtractedClause]:
        """本地快速预去重: 多维度消除明显重复

        按 sub_type 分组，组内两两比较:
        1. raw_text 精确匹配（同一原文跨分块提取的约束）
        2. content 归一化后精确匹配
        3. 字符集 Jaccard >= 0.7

        发现重复时保留更优版本（content 更长 / 有 raw_text 的优先）。
        """
        if len(clauses) <= 1:
            return clauses

        grouped: dict[str, list[ExtractedClause]] = {}
        for c in clauses:
            grouped.setdefault(c.sub_type, []).append(c)

        result: list[ExtractedClause] = []
        for _st, items in grouped.items():
            seen_norms: list[str] = []
            seen_raws: list[str] = []
            unique: list[ExtractedClause] = []

            for c in items:
                norm = re.sub(r'[\s\W]', '', c.content.strip().lower())
                raw_norm = re.sub(r'[\s\W]', '', (c.raw_text or "").strip().lower())
                is_dup = False
                dup_idx = -1

                for i, s_norm in enumerate(seen_norms):
                    # raw_text 精确匹配
                    if raw_norm and seen_raws[i] and raw_norm == seen_raws[i]:
                        is_dup = True
                        dup_idx = i
                        break
                    # content 精确匹配
                    if norm == s_norm:
                        is_dup = True
                        dup_idx = i
                        break
                    # 字符集 Jaccard
                    chars_a, chars_b = set(norm), set(s_norm)
                    union = len(chars_a | chars_b)
                    if union > 0 and len(chars_a & chars_b) / union >= 0.7:
                        is_dup = True
                        dup_idx = i
                        break

                if is_dup and dup_idx >= 0:
                    existing = unique[dup_idx]
                    # 保留更优版本: content 更长 或 有 raw_text 的优先
                    has_better_raw = (c.raw_text and not existing.raw_text)
                    has_longer_content = len(c.content) > len(existing.content)
                    if has_better_raw or (not existing.raw_text and has_longer_content):
                        unique[dup_idx] = c
                        seen_norms[dup_idx] = norm
                        seen_raws[dup_idx] = raw_norm
                else:
                    seen_norms.append(norm)
                    seen_raws.append(raw_norm)
                    unique.append(c)

            result.extend(unique)

        removed = len(clauses) - len(result)
        if removed > 0:
            logger.info(f"本地预去重: {len(clauses)} -> {len(result)} (移除 {removed} 条)")
        return result

    async def _deduplicate(
        self, clauses: list[ExtractedClause]
    ) -> list[ExtractedClause]:
        """同层约束去重（决策模式 + 并行批处理）

        流程:
        1. 本地预去重（纯内存）
        2. 少量 (<= batch_size) 直接单次 LLM 决策
        3. 超量时并行分批 + 收敛
        """
        if len(clauses) <= 1:
            return clauses

        clauses = self._local_pre_dedupe(clauses)
        if len(clauses) <= 1:
            return clauses

        batch_size = settings.WRITING_DEDUP_BATCH_SIZE
        if len(clauses) <= batch_size:
            return await self._dedup_batch(clauses)

        # 并行分批去重
        batches = [
            clauses[i:i + batch_size]
            for i in range(0, len(clauses), batch_size)
        ]
        semaphore = asyncio.Semaphore(settings.WRITING_DEDUP_BATCH_CONCURRENCY)

        async def _process(batch: list[ExtractedClause]) -> list[ExtractedClause]:
            async with semaphore:
                return await self._dedup_batch(batch)

        results = await asyncio.gather(*[_process(b) for b in batches])
        intermediate: list[ExtractedClause] = []
        for batch_result in results:
            intermediate.extend(batch_result)

        # 收敛: 中间结果足够小则再做一轮 LLM 收敛，否则本地兜底
        if len(intermediate) <= batch_size:
            return await self._dedup_batch(intermediate)
        return self._local_pre_dedupe(intermediate)

    async def _dedup_batch(
        self, clauses: list[ExtractedClause]
    ) -> list[ExtractedClause]:
        """单批次 LLM 决策模式去重（含 JSON 错误恢复）

        LLM 仅输出合并决策 (merge_ids + content)，
        本地根据决策执行合并，大幅减少输出 tokens。
        """
        clauses_data = []
        for idx, c in enumerate(clauses):
            item: dict[str, Any] = {"idx": idx, "sub_type": c.sub_type, "content": c.content}
            if c.raw_text:
                item["raw_text_preview"] = c.raw_text[:80]
            clauses_data.append(item)

        clauses_json = json.dumps(clauses_data, ensure_ascii=False, separators=(",", ":"))
        system_msg, user_msg = get_dedup_decision_prompt(clauses_json, len(clauses))

        max_attempts = 1 + max(0, settings.WRITING_DEDUP_JSON_RETRY)
        base_temperature, max_tokens = resolve_runtime_params(
            "writing.clause_dedup_decision",
            0.1,
            settings.WRITING_DEDUP_MAX_TOKENS,
        )
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            temperature = base_temperature if attempt == 0 else 0.0
            runtime_tokens = max_tokens

            if attempt > 0:
                if last_error is not None and self._is_truncation_error(last_error):
                    runtime_tokens = min(int(max_tokens * 1.5), 6000)
                else:
                    runtime_tokens = max(1200, int(max_tokens * 0.75))
                    user_msg += (
                        "\n\n只输出 JSON 数组本体，不要 markdown 代码块，"
                        "不要解释文字。每个元素必须包含 merge_ids/content/sub_type 字段。"
                    )

            try:
                result = await self.llm_client.chat_json(
                    user_message=user_msg,
                    system_message=system_msg,
                    temperature=temperature,
                    max_tokens=runtime_tokens,
                )

                if not isinstance(result, list):
                    logger.warning("去重决策返回非数组，保留原始数据")
                    return clauses

                if not result:
                    return clauses

                decisions = []
                for item in result:
                    if isinstance(item, dict) and "merge_ids" in item:
                        try:
                            decisions.append(ClauseMergeDecision(**item))
                        except (ValidationError, Exception) as e:
                            logger.debug(f"单条决策解析跳过: {e}")

                if not decisions:
                    return clauses

                merged = self._apply_merge_decisions(clauses, decisions)
                logger.info(
                    f"决策模式去重: attempt={attempt + 1}, "
                    f"in={len(clauses)}, decisions={len(decisions)}, out={len(merged)}"
                )
                return merged

            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM 去重失败: attempt={attempt + 1}/{max_attempts}, error={e}"
                )
                if attempt >= max_attempts - 1 or not self._is_json_like_error(e):
                    break

        logger.warning("LLM 去重全部失败，保留原始数据")
        return clauses

    @staticmethod
    def _apply_merge_decisions(
        clauses: list[ExtractedClause],
        decisions: list[ClauseMergeDecision],
    ) -> list[ExtractedClause]:
        """根据 LLM 合并决策在本地执行合并

        未出现在任何 merge_ids 中的条目原样保留。
        合并组取 decision.content 作为新描述，
        raw_text / source_page / score_weight 保留信息最完整的版本。
        """
        merged_indices: set[int] = set()
        new_clauses: list[ExtractedClause] = []

        for decision in decisions:
            valid_ids = [
                idx for idx in decision.merge_ids
                if 0 <= idx < len(clauses)
            ]
            if len(valid_ids) < 2:
                continue

            merged_indices.update(valid_ids)

            # 选择 raw_text 最长的作为保留源
            best = max(
                (clauses[i] for i in valid_ids),
                key=lambda c: len(c.raw_text or ""),
            )

            # 合并后的页范围按组内并集展开(诚实表达), 起点取最小, 终点取最大
            page_starts = [
                clauses[i].source_page_start
                for i in valid_ids
                if clauses[i].source_page_start is not None
            ]
            page_ends = [
                clauses[i].source_page_end
                for i in valid_ids
                if clauses[i].source_page_end is not None
            ]
            merged_start = min(page_starts) if page_starts else best.source_page_start
            merged_end = max(page_ends) if page_ends else best.source_page_end

            new_clauses.append(ExtractedClause(
                sub_type=decision.sub_type,
                content=decision.content,
                raw_text=best.raw_text,
                source_page=best.source_page,
                source_page_start=merged_start,
                source_page_end=merged_end,
                score_weight=best.score_weight,
                metadata=decision.metadata or best.metadata,
            ))

        kept = [c for i, c in enumerate(clauses) if i not in merged_indices]
        return kept + new_clauses

    @staticmethod
    def _is_json_like_error(error: Exception) -> bool:
        if isinstance(error, (json.JSONDecodeError, ValidationError)):
            return True
        text = str(error).lower()
        return any(
            m in text for m in (
                "json", "unterminated string", "expecting ',' delimiter",
                "expecting value", "extra data",
            )
        )

    @staticmethod
    def _is_truncation_error(error: Exception) -> bool:
        return "unterminated string" in str(error).lower()

    # ==================== 跨层去重 ====================

    @staticmethod
    def _cross_layer_dedup_material_notice(
        hard_clauses: list[ExtractedClause],
        structure_clauses: list[ExtractedClause],
    ) -> tuple[list[ExtractedClause], list[ExtractedClause]]:
        """跨层去重: submission_notice vs material_required

        当两层中存在语义重复的条目时，保留 submission_notice 版本，
        移除 material_required 中的重复项。
        """
        notices = [c for c in hard_clauses if c.sub_type == "submission_notice"]
        if not notices:
            return hard_clauses, structure_clauses

        notice_norms = [
            re.sub(r'[\s\W]', '', c.content.strip().lower()) for c in notices
        ]

        kept_structure: list[ExtractedClause] = []
        removed = 0
        for c in structure_clauses:
            if c.sub_type != "material_required":
                kept_structure.append(c)
                continue
            norm = re.sub(r'[\s\W]', '', c.content.strip().lower())
            is_dup = False
            for n_norm in notice_norms:
                chars_a, chars_b = set(norm), set(n_norm)
                union = len(chars_a | chars_b)
                if union > 0 and len(chars_a & chars_b) / union >= 0.65:
                    is_dup = True
                    break
            if is_dup:
                removed += 1
            else:
                kept_structure.append(c)

        if removed > 0:
            logger.info(
                f"跨层去重: material_required 移除 {removed} 条"
                f"(与 submission_notice 重复)"
            )

        return hard_clauses, kept_structure

    # ==================== DB 写入 ====================

    async def _save_clauses(
        self,
        project_id: int,
        document_id: int,
        extracted: list[ExtractedClause],
    ) -> list[WritingClause]:
        """将提取结果写入 writing_clauses 表"""
        layer_map = {
            "disqualification": WritingClauseLayer.hard_constraint,
            "fixed_template": WritingClauseLayer.hard_constraint,
            "format_rule": WritingClauseLayer.hard_constraint,
            "submission_rule": WritingClauseLayer.hard_constraint,
            "submission_notice": WritingClauseLayer.hard_constraint,
            "directory_rule": WritingClauseLayer.structure_constraint,
            "material_required": WritingClauseLayer.structure_constraint,
            "content_required": WritingClauseLayer.structure_constraint,
            "table_format": WritingClauseLayer.structure_constraint,
            "scoring_criterion": WritingClauseLayer.generation_guide,
            "project_context": WritingClauseLayer.generation_guide,
            "emphasis_point": WritingClauseLayer.generation_guide,
            "differentiation_hint": WritingClauseLayer.generation_guide,
            "industry_profile": WritingClauseLayer.generation_guide,
            "bid_structure_analysis": WritingClauseLayer.structure_constraint,
            "bid_directory_spec": WritingClauseLayer.structure_constraint,
        }

        # 系统生成类 sub_type 允许 raw_text/source_page 均为空
        system_generated_sub_types = frozenset({
            "industry_profile",
            "bid_structure_analysis",
            "bid_directory_spec",
        })

        saved: list[WritingClause] = []
        missing_evidence_count = 0
        document_chunks: list[Chunk] = []
        result = await self.db.execute(
            select(Chunk).where(
                Chunk.document_id == document_id,
            ).order_by(Chunk.chunk_index)
        )
        document_chunks = list(result.scalars().all())
        self._report_progress("saving", 0, 1, "整理固定模板")
        template_catalog = await build_fixed_template_catalog_async(
            document_chunks, project_id=project_id,
        )
        if template_catalog:
            logger.info(
                "fixed_template catalog 构建完成: document_id={}, count={}",
                document_id,
                len(template_catalog),
            )

        # 先按"页锚定优先"为每条 LLM 约束确定其唯一认领的 catalog 条目,
        # 只有被认领的条目才跳过补建; 未被认领的条目(如同 key 另一卷的模板)
        # 必须照常补为 system_generated 约束, 避免卷盲吞并。
        claimed_entry_ids = {
            id(matched)
            for ext in extracted
            if ext.sub_type == "fixed_template"
            and (matched := _match_catalog_entry(ext, template_catalog)) is not None
        }
        for entry in template_catalog:
            if id(entry) in claimed_entry_ids:
                continue
            extracted.append(
                ExtractedClause(
                    sub_type="fixed_template",
                    content=f"{entry.title}固定模板，要求投标人按招标文件格式填写使用",
                    raw_text=entry.raw_text,
                    source_page=entry.source_page_start,
                    source_page_start=entry.source_page_start,
                    source_page_end=entry.source_page_end,
                    metadata={
                        "target_section_hint": entry.title,
                        "template_key": entry.template_key,
                        FIXED_TEMPLATE_SNAPSHOT_KEY: entry.snapshot_dict(),
                        "system_generated": True,
                    },
                )
            )

        fixed_template_metadatas: list[dict[str, Any]] = []
        for idx, ext in enumerate(extracted):
            layer = layer_map.get(ext.sub_type)
            if layer is None:
                logger.warning(f"未知 sub_type '{ext.sub_type}'，跳过")
                continue

            has_page_anchor = (
                ext.source_page is not None
                or ext.source_page_start is not None
                or ext.source_page_end is not None
            )
            if (
                ext.sub_type not in system_generated_sub_types
                and not ext.raw_text
                and not has_page_anchor
            ):
                # 最小可核查底线: 用户可见条款必须至少带摘录或页锚点之一,
                # 否则直接拒绝入库, 不再让它出现在用户确认列表里。
                missing_evidence_count += 1
                logger.warning(
                    f"[EvidenceGate] 拒绝用户可见无证据约束: "
                    f"sub_type={ext.sub_type}, content='{(ext.content or '')[:60]}...', "
                    f"raw_text=None, page_anchor=None"
                )
                continue

            # page_start/page_end 作为面向用户的主语义保留;
            # source_page 仅用于 UI 默认打开 PDF 的锚点; 三者任一缺失时互为回退,
            # 保证下游读到的行同时具备可展示范围和可跳转锚点。
            start = ext.source_page_start
            end = ext.source_page_end
            default_page = ext.source_page
            if start is None and end is not None:
                start = end
            if end is None and start is not None:
                end = start
            if default_page is None and start is not None:
                default_page = start
            if start is None and default_page is not None:
                start = default_page
                end = default_page

            metadata_json = dict(ext.metadata or {})
            if ext.sub_type == "fixed_template":
                catalog_entry = _match_catalog_entry(ext, template_catalog)
                if catalog_entry is not None:
                    ext.raw_text = catalog_entry.raw_text
                    start = catalog_entry.source_page_start
                    end = catalog_entry.source_page_end
                    default_page = catalog_entry.source_page_start
                    metadata_json["template_key"] = catalog_entry.template_key
                    metadata_json["target_section_hint"] = catalog_entry.title
                    metadata_json[FIXED_TEMPLATE_SNAPSHOT_KEY] = catalog_entry.snapshot_dict()
                elif FIXED_TEMPLATE_SNAPSHOT_KEY not in metadata_json:
                    snapshot = self._build_fixed_template_snapshot(
                        ext,
                        document_chunks,
                        start,
                        end,
                    )
                    if snapshot is not None:
                        metadata_json[FIXED_TEMPLATE_SNAPSHOT_KEY] = snapshot.to_dict()
                if FIXED_TEMPLATE_SNAPSHOT_KEY in metadata_json:
                    fixed_template_metadatas.append(metadata_json)

            clause = WritingClause(
                project_id=project_id,
                document_id=document_id,
                layer=layer,
                sub_type=ext.sub_type,
                content=ext.content,
                raw_text=ext.raw_text,
                source_page=default_page,
                source_page_start=start,
                source_page_end=end,
                score_weight=ext.score_weight,
                metadata_json=metadata_json or None,
                confirmed=0,
                sort_order=idx,
            )
            self.db.add(clause)
            saved.append(clause)

        # 提交前对 fixed_template 快照做布局保真增补(chunk 文本流丢失
        # 二维版式, 序号/填空值错位; verbatim 通道直接从 PDF 重组视觉行)。
        # 增补失败只降级不阻断, 旧链路行为不变。
        if fixed_template_metadatas:
            doc_result = await self.db.execute(
                select(Document).where(Document.id == document_id)
            )
            source_document = doc_result.scalar_one_or_none()
            enriched_count = await asyncio.to_thread(
                enrich_snapshots_verbatim,
                getattr(source_document, "file_path", None),
                fixed_template_metadatas,
            )
            logger.info(
                "fixed_template verbatim 快照增补: document_id={}, enriched={}/{}",
                document_id,
                enriched_count,
                len(fixed_template_metadatas),
            )

        await self.db.commit()
        for clause in saved:
            await self.db.refresh(clause)

        self._report_progress("saving", 1, 1, f"已保存 {len(saved)} 条约束")
        logger.info(
            f"约束写入完成: project={project_id}, count={len(saved)}, "
            f"missing_evidence={missing_evidence_count}"
        )
        return saved

    @staticmethod
    def _build_fixed_template_snapshot(
        ext: ExtractedClause,
        chunks: list[Chunk],
        source_page_start: Optional[int],
        source_page_end: Optional[int],
    ) -> Optional[FixedTemplateSnapshot]:
        """基于页范围回查招标原文，构造 fixed_template 快照。"""
        if not chunks:
            return None

        selected: list[Chunk] = []
        if source_page_start is not None and source_page_end is not None:
            selected = [
                chunk for chunk in chunks
                if chunk.page_num is not None
                and source_page_start <= chunk.page_num <= source_page_end
            ]
        if not selected and ext.source_page is not None:
            selected = [chunk for chunk in chunks if chunk.page_num == ext.source_page]
        if not selected:
            selected = chunks

        raw_from_chunks = "\n\n".join(
            (chunk.content or "").strip()
            for chunk in selected
            if (chunk.content or "").strip()
        ).strip()
        if not raw_from_chunks:
            return None
        if len(raw_from_chunks) > _FIXED_TEMPLATE_SNAPSHOT_MAX_CHARS:
            raw_from_chunks = raw_from_chunks[:_FIXED_TEMPLATE_SNAPSHOT_MAX_CHARS].rstrip()

        raw_text = (ext.raw_text or "").strip()
        content = (ext.content or "").strip()
        confidence = 0.55
        if raw_text and raw_text in raw_from_chunks:
            confidence = 0.95
        elif raw_text and _content_batch_overlap(raw_text, raw_from_chunks.lower()) >= 0.3:
            confidence = 0.75
        elif content and _content_batch_overlap(content, raw_from_chunks.lower()) >= 0.2:
            confidence = 0.65

        title = str(
            (ext.metadata or {}).get("target_section_hint")
            or content[:80]
            or "fixed_template"
        )
        has_signature = any(kw in raw_from_chunks for kw in ("签字", "盖章", "公章", "日期"))
        is_complete_candidate = confidence >= 0.65 and has_signature
        return FixedTemplateSnapshot(
            title=title,
            source_page_start=source_page_start,
            source_page_end=source_page_end,
            raw_text_from_chunks=raw_from_chunks,
            extraction_confidence=round(confidence, 3),
            is_complete_candidate=is_complete_candidate,
        )

    # ==================== 章节上下文检测 ====================

    _HEADING_RE = re.compile(
        r'^\s*(?:'
        r'第[一二三四五六七八九十百千\d]+[章节篇部]'
        r'|[\d]+(?:\.\d+)*\s'
        r'|[一二三四五六七八九十]+[、.]\s*'
        r'|（[一二三四五六七八九十\d]+）'
        r')',
    )

    _CONTRACT_PHASE_KEYWORDS = frozenset([
        "合同", "履约", "成果提交", "交付物", "验收",
        "项目实施", "合同执行", "工作成果", "付款",
        "违约", "索赔", "保修", "质保",
    ])

    @staticmethod
    def _locate_chunk_position(
        target: Chunk,
        all_chunks: list[Chunk],
    ) -> Optional[int]:
        """返回 target 在 all_chunks 中的真实列表位置: 严禁用业务字段 chunk_index 充当列表下标。chunk_index 在
 历史脏数据（重复/间隙）下不保证等于列表位置，直接索引会越界。
 优先按主键 id 匹配，回退对象 identity；定位不到返回 None。
 """
        target_id = getattr(target, "id", None)
        if target_id is not None:
            for pos, chunk in enumerate(all_chunks):
                if getattr(chunk, "id", None) == target_id:
                    return pos
        for pos, chunk in enumerate(all_chunks):
            if chunk is target:
                return pos
        return None

    @classmethod
    def _detect_section_context(
        cls,
        start_chunk: Chunk,
        all_chunks: list[Chunk],
    ) -> tuple[str, bool]:
        """回溯检测当前批次所属的章节标题

        从 start_chunk 在 all_chunks 中的真实位置向前最多回溯 15 个 chunk，
        在每个 chunk 的文本中从后往前查找匹配的章节标题行。

        Returns:
            (章节标题, 是否属于合同/履约阶段)
        """
        start_pos = cls._locate_chunk_position(start_chunk, all_chunks)
        if start_pos is None:
            return "", False
        for look_back in range(min(start_pos + 1, 15)):
            idx = start_pos - look_back
            if idx < 0:
                break
            text = all_chunks[idx].content or ""
            for line in reversed(text.split('\n')):
                line = line.strip()
                if cls._HEADING_RE.match(line) and 5 < len(line) < 60:
                    is_contract = any(
                        kw in line for kw in cls._CONTRACT_PHASE_KEYWORDS
                    )
                    return line, is_contract
        return "", False

    # ==================== 进度回调 ====================

    def _report_progress(
        self,
        stage: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        """上报进度"""
        if self.progress_callback:
            try:
                self.progress_callback(stage, current, total, message)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")


def get_clause_extractor(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    concurrency: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
    project_id: Optional[int] = None,
) -> ClauseExtractor:
    """工厂函数"""
    return ClauseExtractor(
        db=db,
        llm_client=llm_client,
        concurrency=concurrency,
        progress_callback=progress_callback,
        project_id=project_id,
    )
