"""文档处理阶段 2.5 / 3 / 3.5

职责:
- 阶段 2.5: 目录解析(失败不阻塞后续) + Session 3 保存 toc_map
- 阶段 3.0: 评分原始表证据快照(候选事务证据层, Task2)——必须在本函数内任何评分表
 装配/构建调用(build_scoring_table_chunks 等)之前执行, 捕获装配前的完整物理表
- 阶段 3: 文本分块 -- 把 VL 阶段产出的图片注释 inline 插入页面文本, 再走 LangChain 分块
- 阶段 3.5: 图片证据独立 chunk(避免 VL 内容被分块淹没, BUG 同源风险)

行为契约:
- 心跳 stage="chunking" 按子阶段发文案心跳, 纯文案(无 "/"), 不触发 numeric 进度回退
 防御 (: 让前端 ProgressPanel 渲染子步骤, 避免长时间 "处理中..." 被误判卡死)。
 numeric 进度 (f"{done}/{total}") 仅在 vectorize 阶段出现。
- shadow_summary 在 VECTOR_SHADOW_COMPARE_ENABLED 时计算并写入 base_extra(供 keepalive 透传)
- 评估 evidence_count 仅依赖 effective_skip_vl, 与 VL 实际成功数解耦(模板 chunk 仍可生成)
- 阶段 3.0 的证据捕获代码顺序必须早于本文件内任何评分表装配调用(build_scoring_table_chunks),
 保证数据血缘上消费的是 parsed_doc 尚未被任何装配/降噪步骤触碰过的原始物理表(2309 教训)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from loguru import logger

from app.core.tasks.process.state import ChunkStageResult


async def run_chunk_stage(
    *,
    parsed_doc: Any,
    document_id: int,
    effective_skip_vl: bool,
    base_extra: dict,
    heartbeat: Callable[..., None],
    cancel_check: Callable[[], None] | None = None,
) -> ChunkStageResult:
    """执行 阶段2.5 目录 + 阶段3 文本分块 + 阶段3.5 图片证据 chunk.

    Returns ChunkStageResult(chunks, shadow_summary, evidence_count)
    """
    # 函数内 import: 防循环依赖 + 兼容 monkeypatch
    from app.core.config import settings
    from app.core.observability.metrics import vector_shadow_diff_total
    from app.core.parser import ChunkPlanner
    from app.core.parser.image_extractor import (
        build_natural_evidence_chunk,
        extract_context_for_images,
        generate_image_annotations,
        insert_annotation_near_anchor,
    )
    from app.core.parser.text_cleaner import denoise_pages
    from app.core.parser.toc_parser import TocParser, serialize_toc_map
    from app.core.tasks import process_task as ptm
    from app.core.tasks.cancellation import invoke_cancel_check

    def _check_cancelled() -> None:
        invoke_cancel_check(cancel_check)

    # ========== 阶段2.5: 目录解析 ==========
    _check_cancelled()
    toc_map = None
    try:
        toc_parser = TocParser()
        toc_map = toc_parser.parse_resolved(parsed_doc.pages)
        if toc_map:
            logger.info(
                "[文档处理] 目录解析完成: entries={}, offset={}, confidence={}, enabled={}",
                toc_map.get("total_entries", 0),
                toc_map.get("offset"),
                toc_map.get("confidence"),
                toc_map.get("enabled"),
            )
    except Exception as e:
        logger.warning(f"[文档处理] 目录解析失败(不影响后续): {e}")

    # 目录解析完成后给前端一个可观测子步骤 (纯文案心跳, 无 "/")
    _check_cancelled()
    heartbeat("chunking", "目录解析完成")

    # ===== Session 3: 保存目录 (秒级) =====
    if toc_map:
        _check_cancelled()
        async with ptm.async_session_factory() as db:
            document = await ptm._get_document(db, document_id)
            _check_cancelled()
            if document:
                document.toc_map = serialize_toc_map(toc_map)
                _check_cancelled()
                await db.commit()

    # ========== 阶段3: 文本分块（线程，不阻塞事件循环）==========
    _check_cancelled()
    logger.info("[文档处理] 阶段3: 文本分块")
    heartbeat("chunking", "文本分块中")

    planner = ChunkPlanner()
    all_pages = parsed_doc.pages
    scoring_scope_page_texts: dict[int, str] = {}
    pre_denoise_scoring_sections = []
    pre_denoise_scoring_signals = None
    if bool(getattr(settings, "TENDER_TABLE_STITCH_ENABLED", True)):
        try:
            from app.core.parser.scoring_chunk_builder import (
                scan_sections_by_table_signals,
            )
            from app.core.parser.scoring_section_locator import locate_scoring_sections

            # 与 scoring_chunk_builder._merge_sections 同源:
            # 章节定位 ∪ 表信号回扫，确保 1889 类"无关键词但有评分结构表"的文档
            # 也能通过下游评分捕获的门禁 (P2-1)。
            pre_denoise_scoring_sections = (
                locate_scoring_sections(parsed_doc)
                + scan_sections_by_table_signals(parsed_doc)
            )
        except Exception as e:
            logger.warning(f"[文档处理] 预降噪评分区段定位失败(忽略): {e}")

    # 仅对评分区段及前后1页保留降噪前文本，避免复制整份长文档。
    scope_page_nums = {
        page_num
        for section in pre_denoise_scoring_sections
        for page_num in range(
            max(1, int(getattr(section, "page_start", 1) or 1) - 1),
            int(getattr(section, "page_end", 0) or 0) + 2,
        )
    }
    scoring_scope_page_texts = {
        page.page_num: str(page.text or "")
        for page in all_pages
        if page.page_num in scope_page_nums
    }

    # SchemeReader 信号契约声明“装配/分块前逐字捕获”，同样不能读取降噪后已与
    # 评分行粘连的标题文本。先只读构建，仍在下方既有阶段统一落库和记录日志。
    if bool(getattr(settings, "SCORING_STRUCTURE_SIGNALS_ENABLED", True)):
        try:
            _check_cancelled()
            from app.core.parser.scoring_structure_signals import (
                build_scoring_structure_signals,
            )

            pre_denoise_scoring_signals = await asyncio.to_thread(
                build_scoring_structure_signals,
                parsed_doc,
                pre_denoise_scoring_sections,
            )
            _check_cancelled()
        except Exception as e:
            logger.warning(f"[文档处理] 预降噪评分结构信号捕获失败(稍后回退): {e}")

    # P1: 页级降噪 (剔除重复页眉/页脚 + 合并中文软换行)
    # 必须放在 toc 解析之后, 避免破坏目录"标题…页码"行识别;
    # 必须放在图片标注插入与 plan_parsed_pages 之前, 让噪声不进入 chunk.
    if bool(getattr(settings, "CHUNK_DENOISE_ENABLED", True)):
        _check_cancelled()
        denoise_summary = await asyncio.to_thread(denoise_pages, all_pages)
        _check_cancelled()
        if denoise_summary.get("removed_repeats") or denoise_summary.get("merged_pages"):
            base_extra["chunk_denoise"] = denoise_summary
    # 降噪完成后子步骤心跳
    _check_cancelled()
    heartbeat("chunking", "页面降噪完成")
    for idx, p in enumerate(all_pages):
        _check_cancelled()
        page_text = p.text or ""
        if p.images and not effective_skip_vl:
            window_texts = []
            for offset in range(-2, 3):
                ni = idx + offset
                if 0 <= ni < len(all_pages) and ni != idx:
                    t = all_pages[ni].text or ""
                    if t.strip():
                        window_texts.append(t.strip())
            combined_prev = "\n".join(window_texts)

            context = extract_context_for_images(
                current_page_text=page_text,
                prev_page_text=combined_prev,
            )
            image_annotation = generate_image_annotations(
                p.images,
                context=context,
                # getattr 兼容 SimpleNamespace 测试桩
                full_page_text=getattr(p, "full_page_visual_content", ""),
            )
            if image_annotation:
                page_text = insert_annotation_near_anchor(
                    page_text, image_annotation
                )
        p.text = page_text

    shadow_summary = None
    if bool(getattr(settings, "VECTOR_SHADOW_COMPARE_ENABLED", False)):
        _check_cancelled()
        shadow_summary = await asyncio.to_thread(planner.shadow_compare, all_pages)
        _check_cancelled()
        base_extra["vector_shadow"] = shadow_summary.to_dict()
        if shadow_summary.new_count != shadow_summary.old_count:
            vector_shadow_diff_total.labels(flow="vectorize", diff_type="chunk_count").inc()
        if shadow_summary.new_max_chars != shadow_summary.old_max_chars:
            vector_shadow_diff_total.labels(flow="vectorize", diff_type="max_chars").inc()

    _check_cancelled()
    # 进入 LangChain 分块前给前端可观测子步骤
    heartbeat("chunking", "正在生成分块")
    chunks = await asyncio.to_thread(planner.plan_parsed_pages, all_pages)
    _check_cancelled()
    logger.info(f"[文档处理] 文本分块完成: {len(chunks)}个分块")
    # 分块数量已知, 后续可能跨页拼接与图片证据处理, 透传 "分块完成" 让前端确认数量
    heartbeat("chunking", f"已生成 {len(chunks)} 个分块")

    # ========== 阶段3.4: 评分表跨页拼接 -> 整表 chunk（P2）==========
    # 仅当文档含"评标办法/评分表"等评分区段时才生成（投标文件不会命中），
    # 与逐行 table_row chunk 并存，供 scoring_extractor 看到完整跨页评分表。
    if bool(getattr(settings, "TENDER_TABLE_STITCH_ENABLED", True)):
        try:
            _check_cancelled()
            from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

            scoring_chunks = await asyncio.to_thread(
                build_scoring_table_chunks,
                parsed_doc,
                planner,
                len(chunks),
                pre_denoise_scoring_sections,
                scope_page_texts=scoring_scope_page_texts,
            )
            _check_cancelled()
            if scoring_chunks:
                chunks.extend(scoring_chunks)
                base_extra["scoring_table_chunks"] = len(scoring_chunks)
        except Exception as e:
            logger.warning(f"[文档处理] 评分表整表 chunk 生成失败(忽略): {e}")

    # ========== 阶段3.4b: 评分结构信号快照（SchemeReader 阶段0）==========
    # 装配前逐字捕获边界信号(合计行/表头行/标题样行/声明句), 落 documents.scoring_signals,
    # 供提取阶段的评分结构判读器消费; 失败仅告警不阻塞。
    if bool(getattr(settings, "SCORING_STRUCTURE_SIGNALS_ENABLED", True)):
        try:
            _check_cancelled()
            from app.core.parser.scoring_structure_signals import (
                build_scoring_structure_signals,
                summarize_signals_for_log,
            )

            signals = pre_denoise_scoring_signals
            if signals is None:
                signals = await asyncio.to_thread(
                    build_scoring_structure_signals,
                    parsed_doc,
                    pre_denoise_scoring_sections,
                )
            _check_cancelled()
            if signals:
                async with ptm.async_session_factory() as db:
                    document = await ptm._get_document(db, document_id)
                    _check_cancelled()
                    if document:
                        document.scoring_signals = signals
                        await db.commit()
                logger.info(
                    "[文档处理] 评分结构信号快照已保存: {}",
                    summarize_signals_for_log(signals),
                )
        except Exception as e:
            logger.warning(f"[文档处理] 评分结构信号快照捕获失败(忽略): {e}")

    # ========== 阶段3.5: 图片证据独立chunk ==========
    evidence_count = 0
    if not effective_skip_vl:
        for idx, p in enumerate(all_pages):
            _check_cancelled()
            if not p.images:
                continue
            # 当前页文字放窗口首位, 让本页标题进入图片证据上下文
            window_texts = []
            own_text = (p.text or "").strip()
            if own_text:
                window_texts.append(own_text)
            for offset in range(-2, 3):
                ni = idx + offset
                if 0 <= ni < len(all_pages) and ni != idx:
                    t = all_pages[ni].text or ""
                    if t.strip():
                        window_texts.append(t.strip())
            ctx_window = "\n".join(window_texts)

            evidence_text = build_natural_evidence_chunk(
                page_num=p.page_num,
                images=p.images,
                context=ctx_window,
                full_page_text=getattr(p, "full_page_visual_content", ""),
            )
            if evidence_text and len(evidence_text) >= 20:
                _check_cancelled()
                chunk = planner.build_manual_chunk(
                    content=evidence_text,
                    page_num=p.page_num,
                    chunk_type="image_evidence",
                    parent_block_id=f"image-evidence:{p.page_num}",
                    section_path="图片证据",
                )
                chunk.chunk_index = len(chunks)
                chunk.metadata["is_evidence"] = True
                chunks.append(chunk)
                evidence_count += 1

    if evidence_count:
        logger.info(f"[文档处理] 创建{evidence_count}个图片证据独立chunk")
    # 图片证据分块完成 (若跳过 VL, 此处文案仍可达前端)
    _check_cancelled()
    heartbeat(
        "chunking",
        "图片证据分块完成" if evidence_count else "图片证据分块跳过",
    )

    _check_cancelled()
    return ChunkStageResult(
        chunks=chunks,
        shadow_summary=shadow_summary,
        evidence_count=evidence_count,
    )
