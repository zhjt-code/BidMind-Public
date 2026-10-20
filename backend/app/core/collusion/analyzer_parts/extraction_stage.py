"""串标分析器 S1 抽取阶段（CollusionAnalyzer 类内方法 → 子模块函数）

Task 8 Step 6：从 CollusionAnalyzer 主类迁出 S1 阶段 19 个类内方法到子模块。
主 analyzer.py 保留同名 thin forwarder 兼容外部调用。

子模块函数签名规范（方案 B，与 Task 6 略偏离的折中）：
- 函数名 = 原 method 名**去前导下划线**（_extract_tender_format -> extract_tender_format）。
- 第一参数 self（命名保留，由 CollusionAnalyzer 主类 thin forwarder 显式传入）。
- 函数体内 self.xxx / self._xxx 调用保持原样，通过主类 thin forwarder 链路工作。

迁出范围（按 plan §2.4，不迁 _save_extraction / _extraction_to_dict）：
- S1a 招标格式提取：extract_tender_format
- S1b 投标信息提取（编排+单文件+多路由+质量门）：extract_all_bid_docs /
  extract_one_bid_doc / run_multi_route_extraction / extract_route_payload /
  build_extraction_route_contexts / check_extraction_quality
- chunk 选择：get_document_chunks / select_chunks_for_route /
  format_extraction_chunks
- 元数据：build_collusion_metadata_info / read_pdf_metadata
- 行业画像：generate_industry_profile
- 共享读：load_documents / get_project_owner_id / get_kb_usage_allowed_ids /
  get_document_text / query_extraction

依赖说明（method body 中通过 self 访问的主类资源）：
- self._db / self._project_id / self._db_lock / self._heartbeat / self._call_llm /
  self._save_extraction / self._extraction_to_dict 等。
- 各方法独立完整移植，子模块函数之间互相调用通过 self.* (走主类 thin forwarder)
  或显式调用同模块函数（按需）。
"""

from __future__ import annotations


# 补全 import 修复
from app.core.ai.prompt_registry import resolve_user_template
from app.core.collusion.extraction_routes import build_empty_extraction

import asyncio
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.core.config import settings, get_collusion_extract_timeout
from app.core.database import async_session_factory
from app.core.collusion.analyzer_parts.constants import (
    _EXTRACTION_FAIL_THRESHOLD,
    _IMAGE_BUDGET,
    _MAX_TENDER_CHARS,
)
from app.core.collusion.analyzer_parts.extraction_validation import (
    _empty_extraction,
)
from app.core.collusion.extraction_routes import (
    EXTRACTION_ROUTE_CONFIGS,
    ExtractionRouteConfig,
    get_extraction_route,
    merge_route_extractions,
)
from app.core.collusion.industry_profiler import CollusionIndustryProfiler
from app.core.collusion.prompts import (
    COLLUSION_TENDER_FORMAT_PROMPT,
    build_collusion_extract_route_prompt,
)
from app.core.collusion.analyzer_parts.constants import ExtractionQualityError
from app.core.parser.image_extractor import (
    is_annotation_chunk_content,
    is_evidence_chunk_content,
    is_image_related_chunk_content,
)
from app.models.collusion_extraction import CollusionExtraction
from app.models.document import Document
from app.models.project import Project
from app.schemas.collusion import LLMExtractionResult, LLMTenderFormatResult


async def extract_tender_format(self, tender_doc: Document) -> Optional[dict]:
    """提取招标文件格式要求（白名单过滤器）"""
    doc_text = await self._get_document_text(tender_doc.id, max_chars=_MAX_TENDER_CHARS)
    if not doc_text:
        return None

    try:
        result = await self._call_llm(
            user_message=resolve_user_template(
                "collusion.tender_format_extract", COLLUSION_TENDER_FORMAT_PROMPT
            ).format(tender_text=doc_text),
            response_model=LLMTenderFormatResult,
            timeout=get_collusion_extract_timeout(),
            max_tokens=1500,
        )
        logger.info(f"[Analyzer] 招标格式提取完成")
        return result
    except Exception as e:
        logger.warning(f"[Analyzer] 招标格式提取失败（使用空格式）: {e}")
        return None


async def generate_industry_profile(
    self,
    tender_text: Optional[str] = None,
    bid_extractions: Optional[list[dict]] = None,
) -> Optional[dict]:
    """生成行业画像（完全基于上传材料，失败不阻塞流程）"""
    self._heartbeat("S1d", "生成行业画像")
    profiler = CollusionIndustryProfiler(project_id=self._project_id)
    profile = await profiler.generate(
        tender_text=tender_text,
        bid_extractions=bid_extractions,
    )
    if profile:
        self._heartbeat(
            "S1d",
            f"行业: {profile.get('industry_type', '?')} ({profile.get('confidence', 'unknown')})",
        )
    return profile


async def extract_all_bid_docs(self, bid_docs: list[Document]) -> list[dict]:
    """并行提取所有投标文件信息（gather 保序 + 回调报进度）"""
    completed = 0
    _progress_lock = asyncio.Lock()

    async def _wrap(doc: Document) -> dict:
        nonlocal completed
        result = await self._extract_one_bid_doc(doc)
        async with _progress_lock:
            completed += 1
            self._heartbeat(
                "S1b",
                f"{completed}/{len(bid_docs)} 份",
                {
                    "substage": "extract_bid_docs",
                    "current": completed,
                    "total": len(bid_docs),
                    "stage_progress_pct": int(completed / len(bid_docs) * 100),
                    "message": f"已完成 {completed}/{len(bid_docs)} 份投标文件提取",
                    "document_id": doc.id,
                },
            )
        return result

    return list(await asyncio.gather(*[_wrap(doc) for doc in bid_docs]))


async def check_extraction_quality(self, bid_docs: list[Document]) -> None:
    """检查提取失败比例，超过阈值则中止分析"""
    bid_doc_ids = [d.id for d in bid_docs]
    # 使用按需短 session 而非长持有 self._db, 避免连接被服务端
    # idle 超时关闭后再读触发 InterfaceError; 与 教训一致。
    async with async_session_factory() as session:
        failed_q = await session.execute(
            select(CollusionExtraction.document_id, CollusionExtraction.error_message)
            .where(
                CollusionExtraction.project_id == self._project_id,
                CollusionExtraction.extraction_status == "failed",
                CollusionExtraction.document_id.in_(bid_doc_ids),
            )
        )
        failed_rows = failed_q.fetchall()

    if not failed_rows:
        return

    if len(failed_rows) <= 1:
        logger.info(
            f"[Analyzer] 提取质量: {len(failed_rows)}/{len(bid_docs)} 失败, "
            f"允许继续(<=1份失败)"
        )
        return

    fail_rate = len(failed_rows) / len(bid_docs)
    if fail_rate <= _EXTRACTION_FAIL_THRESHOLD:
        return

    doc_map = {d.id: d.filename for d in bid_docs}
    failed_names = []
    for doc_id, err_msg in failed_rows:
        fname = doc_map.get(doc_id, f"文件{doc_id}")
        failed_names.append(fname)
        logger.warning(f"[Analyzer] 提取失败详情: doc_id={doc_id}, file={fname}, error={err_msg}")

    raise ExtractionQualityError(
        f"以下文件信息提取失败: {', '.join(failed_names)}。"
        f"请重新发起分析，系统将自动跳过已成功的文件，仅重试失败部分。"
    )


async def extract_one_bid_doc(self, doc: Document) -> dict:
    """提取单份投标文件结构化信息（分路提取 + 聚合 + 断点续跑）"""
    existing = await self._query_extraction(doc.id)
    if existing and existing.extraction_status == "success":
        logger.info(f"[Analyzer] 跳过已提取文件: doc_id={doc.id}")
        return self._extraction_to_dict(existing)

    route_contexts = await self._build_extraction_route_contexts(doc.id)
    available_contexts = {
        route_name: payload
        for route_name, payload in route_contexts.items()
        if payload.get("context")
    }

    if not available_contexts:
        logger.warning(f"[Analyzer] 文件无文本内容: doc_id={doc.id}")
        empty = _empty_extraction()
        await self._save_extraction(doc.id, "bid", empty, status="empty")
        return empty

    max_attempts = 2
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            extraction = await self._run_multi_route_extraction(
                doc=doc,
                route_contexts=available_contexts,
            )
            extraction["metadata_info"] = self._build_collusion_metadata_info(
                extraction=extraction,
                file_path=doc.file_path,
            )
            await self._save_extraction(doc.id, "bid", extraction, status="success")
            logger.info(
                f"[Analyzer] 信息提取完成: doc_id={doc.id}, "
                f"routes={list(available_contexts.keys())}"
            )
            return extraction
        except asyncio.TimeoutError:
            timeout = get_collusion_extract_timeout()
            last_error = f"多路提取超时（{timeout}秒）"
            logger.warning(
                f"[Analyzer] 文件多路提取超时: doc_id={doc.id}, "
                f"timeout={timeout}s, attempt={attempt}/{max_attempts}"
            )
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(
                f"[Analyzer] 文件多路提取失败: doc_id={doc.id}, "
                f"err={last_error}, attempt={attempt}/{max_attempts}"
            )

        if attempt < max_attempts:
            await asyncio.sleep(2 * attempt)

    empty = _empty_extraction()
    truncated_error = (last_error[:497] + "...") if len(last_error) > 500 else last_error
    await self._save_extraction(
        doc.id, "bid", empty, status="failed",
        error_message=truncated_error,
    )
    return empty


async def run_multi_route_extraction(
    self,
    *,
    doc: Document,
    route_contexts: dict[str, dict],
) -> dict:
    """执行多路并行提取并合并结果。"""
    tasks = [
        self._extract_route_payload(
            doc=doc,
            route_name=route_name,
            context_meta=context_meta,
        )
        for route_name, context_meta in route_contexts.items()
    ]
    route_results = await asyncio.gather(*tasks)
    output_map = {route_name: payload for route_name, payload in route_results}
    return merge_route_extractions(
        route_outputs=output_map,
        route_context_meta=route_contexts,
    )


async def extract_route_payload(
    self,
    *,
    doc: Document,
    route_name: str,
    context_meta: dict,
) -> tuple[str, dict]:
    """提取单一路由内容。"""
    config = get_extraction_route(route_name)
    if not config:
        return route_name, build_empty_extraction()

    timeout = get_collusion_extract_timeout()
    async with self._semaphore:
        result = await self._call_llm(
            user_message=build_collusion_extract_route_prompt(
                doc_name=doc.filename,
                doc_text=context_meta.get("context", ""),
                route_name=route_name,
                target_fields=list(config.target_fields),
            ),
            response_model=LLMExtractionResult,
            timeout=timeout,
        )
    return route_name, result


async def build_extraction_route_contexts(self, doc_id: int) -> dict[str, dict]:
    """按路由从 chunk 中组装提取上下文。"""
    chunks = await self._get_document_chunks(doc_id)
    contexts: dict[str, dict] = {}
    for config in EXTRACTION_ROUTE_CONFIGS:
        selected = self._select_chunks_for_route(chunks, config)
        contexts[config.route_name] = {
            "route_name": config.route_name,
            "target_fields": list(config.target_fields),
            "char_budget": config.max_chars,
            "chunk_count": len(selected),
            "context": self._format_extraction_chunks(selected, config.max_chars),
        }
    return contexts


async def get_document_chunks(self, doc_id: int) -> list[dict]:
    """读取文档全部 chunk，供分路提取复用。"""
    from app.models.chunk import Chunk

    # 短 session, 与 LLM 长调用解耦, 不污染主 session 连接生命周期。
    async with async_session_factory() as session:
        rows = await session.execute(
            select(
                Chunk.id,
                Chunk.content,
                Chunk.page_num,
                Chunk.chunk_type,
                Chunk.chunk_index,
            )
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index)
        )
    return [
        {
            "id": row.id,
            "content": row.content or "",
            "page_num": row.page_num,
            "chunk_type": row.chunk_type,
            "chunk_index": row.chunk_index,
        }
        for row in rows.fetchall()
        if (row.content or "").strip()
    ]


def select_chunks_for_route(
    self,
    chunks: list[dict],
    config: ExtractionRouteConfig,
) -> list[dict]:
    """根据路由配置选择 chunk。"""
    selected: list[dict] = []
    seen_ids: set[int] = set()
    matched_pages: set[int] = set()

    for chunk in chunks:
        page_num = chunk.get("page_num")
        if (
            config.front_page_limit > 0
            and page_num is not None
            and page_num <= config.front_page_limit
        ):
            seen_ids.add(chunk["id"])
            selected.append(chunk)
            if page_num is not None:
                matched_pages.add(page_num)

    if config.route_name == "evidence":
        for chunk in chunks:
            if not is_image_related_chunk_content(chunk.get("content", "")):
                continue
            if chunk["id"] not in seen_ids:
                seen_ids.add(chunk["id"])
                selected.append(chunk)
                if chunk.get("page_num") is not None:
                    matched_pages.add(chunk["page_num"])
    else:
        for chunk in chunks:
            content = chunk.get("content", "")
            if not any(keyword in content for keyword in config.keywords):
                continue
            if chunk["id"] not in seen_ids:
                seen_ids.add(chunk["id"])
                selected.append(chunk)
            if chunk.get("page_num") is not None:
                matched_pages.add(chunk["page_num"])

    if config.include_image_chunks and matched_pages:
        for chunk in chunks:
            if (
                chunk.get("page_num") in matched_pages
                and is_image_related_chunk_content(chunk.get("content", ""))
                and chunk["id"] not in seen_ids
            ):
                seen_ids.add(chunk["id"])
                selected.append(chunk)

    selected.sort(
        key=lambda item: (
            item.get("page_num") if item.get("page_num") is not None else 10**9,
            item.get("chunk_index", 0),
        )
    )
    return selected


def format_extraction_chunks(self, chunks: list[dict], max_chars: int) -> str:
    """将 chunk 列表压缩为提取上下文。"""
    if not chunks:
        return ""

    parts: list[str] = []
    total = 0
    for chunk in chunks:
        page_num = chunk.get("page_num")
        chunk_type = chunk.get("chunk_type") or "text"
        prefix = f"[第{page_num}页][{chunk_type}] " if page_num else f"[{chunk_type}] "
        line = prefix + str(chunk.get("content") or "").strip()
        if not line.strip():
            continue
        if total + len(line) > max_chars:
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


def build_collusion_metadata_info(self, *, extraction: dict, file_path: str) -> dict:
    """补齐串标 D8 所需元数据，并保留提取 trace。

    P2-1: 当 settings.COLLUSION_DOCX_STRUCTURE_ENABLED 开启时，同时将
    docx_structure（toc_headings/font_families/margins/headers_footers）挂到
    extraction["docx_structure"]，供 D6FormatAlgorithmicGate 使用；失败写
    空 dict，让 Gate 原生走"结构不可用→pass"分支，不影响主流程。
    """
    existing = extraction.get("metadata_info") or {}
    metadata_info = dict(existing) if isinstance(existing, dict) else {}
    trace = metadata_info.get("extraction_trace")
    raw_pdf_meta = self._read_pdf_metadata(file_path)

    key_map = {
        "creator": "creator_tool",
        "author": "author",
        "producer": "producer",
        "title": "title",
        "subject": "subject",
        "keywords": "keywords",
        "creationdate": "creation_date",
        "moddate": "mod_date",
    }
    for raw_key, target_key in key_map.items():
        value = raw_pdf_meta.get(raw_key)
        if value:
            metadata_info[target_key] = value
    if raw_pdf_meta:
        metadata_info["raw_pdf_metadata"] = raw_pdf_meta
    if trace:
        metadata_info["extraction_trace"] = trace

    # P2-1: docx_structure 采集（灰度 + 失败兜底）
    if settings.COLLUSION_DOCX_STRUCTURE_ENABLED:
        try:
            from app.core.collusion.docx_structure_extractor import (
                extract_docx_structure,
            )
            structure = extract_docx_structure(file_path)
            if structure:
                extraction["docx_structure"] = structure
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[Analyzer] docx_structure 采集失败 file={file_path} err={exc}"
            )

    # 生产端经 CollusionExtractionMetadata 校验后再落 dict，阻止
    # 子键拼写漂移 / 类型异常静默通过；校验失败 warning + 返回空 dict，保持
    # 返回类型为 dict，下游消费侧零破坏
    try:
        from app.schemas.collusion import CollusionExtractionMetadata
        validated = CollusionExtractionMetadata.model_validate(metadata_info)
        return validated.model_dump(exclude_none=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[Analyzer] metadata_info schema 校验失败 file={file_path} err={exc}"
        )
        return {}


def read_pdf_metadata(self, file_path: str) -> dict[str, str]:
    """只读抽取 PDF metadata，避免再次全量解析文档。"""
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            metadata = pdf.metadata or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[Analyzer] 读取PDF元数据失败: file={file_path}, err={exc}")
        return {}

    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        key_norm = str(key or "").strip().lower()
        value_norm = str(value or "").strip()
        if key_norm and value_norm:
            normalized[key_norm] = value_norm
    return normalized


async def load_documents(self) -> list[Document]:
    # 短 session, 解耦长流程连接持有期。
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Document).where(Document.project_id == self._project_id)
        )
        return list(rows.scalars().all())


async def get_project_owner_id(self) -> int | None:
    """读取项目所有者，用于动态知识库用户级隔离。: 加缓存避免每对并发对比都查 DB; 使用短 session 而非共享 self._db,
 防止与并发对比协程争抢连接池连接 (历史 教训)。
 """
    if self._project_owner_id_loaded:
        return self._project_owner_id_cache
    from app.core.database import async_session_factory
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Project.owner_id).where(Project.id == self._project_id)
        )
        owner_id = rows.scalar_one_or_none()
    normalized = int(owner_id) if owner_id is not None else None
    self._project_owner_id_cache = normalized
    self._project_owner_id_loaded = True
    return normalized


async def get_kb_usage_allowed_ids(self) -> list[int]:
    """读取串标项目级知识库授权配置；未启用或非 collusion_check 返回 []。

    默认未启用即不允许向 pair / text 维度 Prompt 注入任何动态知识库内容。
    允许的资料 ID 列表用于检索层 SQL 限定 d.id IN (...)，避免越权召回。
    """
    if self._kb_usage_loaded:
        return list(self._kb_usage_allowed_ids)
    from app.core.database import async_session_factory
    from app.core.knowledge import load_usage_for_project

    try:
        async with async_session_factory() as session:
            usage = await load_usage_for_project(session, self._project_id)
    except Exception as exc:
        logger.debug(
            f"[Analyzer] 读取项目知识库授权配置失败(降级为未启用): "
            f"project={self._project_id}, err={exc}"
        )
        usage = None
    active = bool(usage and usage.is_active_for_flow("collusion_check"))
    ids = list(usage.selected_kb_document_ids) if active and usage else []
    self._kb_usage_active = active
    self._kb_usage_allowed_ids = ids
    self._kb_usage_loaded = True
    return list(ids)


async def get_document_text(self, doc_id: int, max_chars: int) -> str:
    """拼接文档文本，正文和图片证据分区呈现

    图片标注 chunk 与自然语言 evidence chunk 都可能包含证书编号、
    人员信息等关键内容，但在原始 chunk 序列中可能排在文档后段被截断。
    本方法将正文、结构化标注、自然语言证据分开收集，各自独立截断，
    确保图片相关信息不因正文预算被挤掉。
    """
    from app.models.chunk import Chunk

    # 短 session, 解耦长流程连接持有期。
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Chunk.content, Chunk.page_num)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index)
        )
        chunks = rows.fetchall()

    annotation_budget = min(_IMAGE_BUDGET, max_chars // 5)
    evidence_budget = min(_IMAGE_BUDGET, max_chars // 5)
    text_budget = max_chars - annotation_budget - evidence_budget

    text_parts: list[str] = []
    annotation_parts: list[str] = []
    evidence_parts: list[str] = []
    text_total = 0
    annotation_total = 0
    evidence_total = 0

    for chunk in chunks:
        content = chunk.content or ""
        if not content.strip():
            continue

        is_annotation = is_annotation_chunk_content(content)
        is_evidence = is_evidence_chunk_content(content)

        if is_annotation:
            if annotation_total + len(content) > annotation_budget:
                remaining = annotation_budget - annotation_total
                if remaining > 100:
                    annotation_parts.append(content[:remaining] + "...")
                continue
            page_info = f"(第{chunk.page_num}页)" if chunk.page_num else ""
            annotation_parts.append(f"{page_info} {content}" if page_info else content)
            annotation_total += len(content)
        elif is_evidence:
            if evidence_total + len(content) > evidence_budget:
                remaining = evidence_budget - evidence_total
                if remaining > 100:
                    evidence_parts.append(content[:remaining] + "...")
                continue
            page_info = f"(第{chunk.page_num}页)" if chunk.page_num else ""
            evidence_parts.append(f"{page_info} {content}" if page_info else content)
            evidence_total += len(content)
        else:
            if text_total + len(content) > text_budget:
                remaining = text_budget - text_total
                if remaining > 100:
                    text_parts.append(content[:remaining] + "...")
                text_total = text_budget
                continue
            text_parts.append(content)
            text_total += len(content)

    result = "\n".join(text_parts)

    if annotation_parts:
        result += (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "【以下为文档中图片附件的结构化标注结果】\n"
            + "\n".join(annotation_parts)
        )

    if evidence_parts:
        result += (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "【以下为文档中图片附件的自然语言证据摘要】\n"
            + "\n".join(evidence_parts)
        )

    return result


async def query_extraction(self, doc_id: int) -> Optional[CollusionExtraction]:
    """查询单文档 extraction 记录（按需短 session, 与长流程主连接解耦）: 不再使用 self._db 长持有的连接, 改为独立短 session;
 短 session 自带隔离, 不再需要 _db_lock 串行化保护。
 """
    async with async_session_factory() as session:
        rows = await session.execute(
            select(CollusionExtraction).where(
                CollusionExtraction.project_id == self._project_id,
                CollusionExtraction.document_id == doc_id,
            )
        )
        return rows.scalar_one_or_none()
