"""全文一致性检查

在全部章节生成完毕后执行五项检查:
1. 硬约束合规检查(算法): 废标条款是否被违反 — P1.1 重写, 结构化禁止动作探测
2. 固定措辞检查(算法): fixed_template内容是否被篡改 — P1.1 重写, 基于 raw_text diff
3. 必填完整性检查(算法): 必填章节是否都有内容
4. 交叉引用检查(AI): 扩展正则 + 失败分级 — P1.1 更新
5. 术语一致性检查(算法): 同一实体在全文中名称是否统一
"""

import difflib
import json
import re
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.config import settings
from app.core.contracts.failure_state import FailureState
from app.core.observability.guard_instrument import observe_guard
from app.core.observability.metrics import cross_reference_check_failure_total
from app.core.writer.clause_coverage_checker import build_clause_coverage_issues
from app.core.writer.disqualification_detector import find_disqualification_issues
from app.core.writer.fixed_template import match_template_title
from app.core.writer.fixed_template_contract import resolve_fixed_template_source
from app.core.writer.fixed_template_validator import validate_fixed_template
from app.core.writer.industry_profile_view import extract_profile_terminology
from app.core.writer.prompts.section_compose import get_cross_ref_check_prompt
from app.core.writer.scoring_response_checker import build_scoring_response_issues
from app.core.writer.technical_task_coverage_checker import (
    build_technical_task_coverage_issues,
)
from app.core.writer.terminology_consistency import find_term_variant_issues
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.models.writing_outline_node import WritingOutlineNode
from app.models.writing_section_draft import WritingSectionDraft

# P1.1 扩展交叉引用正则: 两类表达并列
# a) 见/详见/参见/参照/参考/如上述/如前所述 + 内容 + 章/节/部分/条/附件/附录
# b) 附录 + 标识符 (A/B/技术要求等, 1-10 字), 此类本身自成引用, 无需后缀
_CROSS_REF_PATTERN = re.compile(
    r"(?:(?:见|详见|参见|参照|参考|如上述|如前所述)\s*"
    r"[「「\"'']?(.{2,20}?)[」」\"'']?"
    r"(?:章|节|部分|条|附件|附录))"
    r"|"
    r"(?:附录\s*([A-Za-z0-9\u4e00-\u9fff]{1,10}))"
)

# P1.1 固定措辞 diff 相似度阈值; < 0.7 视为被篡改
_FIXED_WORDING_SIMILARITY_THRESHOLD = 0.7


class ConsistencyChecker:
    """全文一致性检查"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)

    async def check(self, project_id: int) -> list[dict]:
        """执行全部一致性检查

        Returns:
            问题列表，每项: {type, severity, node_id, message, detail}
        """
        drafts = await self._load_all_current_drafts(project_id)
        nodes = await self._load_all_nodes(project_id)
        hard_clauses = await self._load_hard_constraints(project_id)
        all_clauses = await self._load_all_clauses(project_id)

        logger.info(
            f"一致性检查启动: project={project_id}, "
            f"drafts={len(drafts)}, nodes={len(nodes)}, hard_clauses={len(hard_clauses)}"
        )

        issues: list[dict] = []

        disq = self._check_disqualification(drafts, hard_clauses)
        issues.extend(disq)
        logger.info(f"  废标条款检查: {len(disq)}个问题")

        wording = self._check_fixed_wording(drafts, nodes, hard_clauses)
        issues.extend(wording)
        logger.info(f"  固定措辞检查: {len(wording)}个问题")

        completeness = self._check_completeness(drafts, nodes)
        issues.extend(completeness)
        logger.info(f"  必填完整性检查: {len(completeness)}个问题")

        cross_ref = await self._check_cross_references(drafts, nodes)
        issues.extend(cross_ref)
        logger.info(f"  交叉引用检查: {len(cross_ref)}个问题")

        terminology = self._check_terminology(
            drafts,
            profile_terms=extract_profile_terminology(
                self._find_industry_profile_content(all_clauses)
            ),
        )
        issues.extend(terminology)
        logger.info(f"  术语一致性检查: {len(terminology)}个问题")

        # B5.5: Writer Gate Pipeline (post_pipeline) - 全局粒度
        self._run_writer_post_pipeline_gate(project_id, drafts, nodes, hard_clauses, issues)

        # 评分项响应专项检查(开关开时): 评分项从覆盖通道剥离, 走三级语义专项检查, 防双报
        scoring_check_on = settings.WRITING_SCORING_RESPONSE_CHECK_ENABLED
        coverage = build_clause_coverage_issues(
            all_clauses,
            list(drafts.values()),
            exclude_sub_types={"scoring_criterion"} if scoring_check_on else None,
        )
        issues.extend(coverage)
        logger.info(f"  约束覆盖检查: {len(coverage)}个问题")

        if scoring_check_on:
            scoring_issues = build_scoring_response_issues(all_clauses, list(drafts.values()))
            issues.extend(scoring_issues)
            logger.info(f"  评分项响应检查: {len(scoring_issues)}个问题")

        task_coverage = build_technical_task_coverage_issues(
            all_clauses,
            list(drafts.values()),
        )
        issues.extend(task_coverage)
        logger.info(f"  技术测试任务覆盖检查: {len(task_coverage)}个问题")

        logger.info(f"一致性检查完成: project={project_id}, issues={len(issues)}")
        return issues

    def _run_writer_post_pipeline_gate(
        self,
        project_id: int,
        drafts: list,
        nodes: list,
        hard_clauses: list,
        issues: list[dict],
    ) -> None:
        """B5.5 Writer post_pipeline Gate Pipeline, fail-safe.

        Feature flag: WRITER_GATE_PIPELINE_RATIO (via should_use_gate_pipeline).
        Gate 可能追加 issue 到 issues 列表，但不直接修改草稿内容。
        """
        try:
            from app.core.ai.gates import build_pipeline_for, should_use_gate_pipeline
            from app.core.ai.gates.base import GateContext
        except Exception:
            return

        if not should_use_gate_pipeline("writer", project_id):
            return

        try:
            _ctx = GateContext(
                flow="writer",
                stage="post_pipeline",
                target=issues,
                evidence={
                    "drafts": drafts,
                    "outline_nodes": nodes,
                    "hard_clauses": hard_clauses,
                    "issues": issues,
                },
                config={"project_id": project_id},
            )
            _pipeline = build_pipeline_for("writer")
            _pipeline.run(_ctx)
        except Exception as e:
            logger.warning(f"Writer post_pipeline Gate Pipeline degraded: {e}")

    # ==================== 1. 硬约束合规 (P1.1 结构化重写) ====================

    @observe_guard("_check_disqualification", "writer")
    def _check_disqualification(
        self,
        drafts: dict[int, WritingSectionDraft],
        clauses: list[WritingClause],
    ) -> list[dict]:
        """检查废标条款是否在全文中被违反。

        P1.1: 改为结构化检测 (见 disqualification_detector.py), 不再使用
        "关键词 + 否定词" 的伪算法。
        """
        full_text = " ".join(d.content or "" for d in drafts.values())
        return find_disqualification_issues(full_text, clauses)

    # ==================== 2. 固定措辞检查 (P1.1 基于 raw_text diff) ====================

    @observe_guard("_check_fixed_wording", "writer")
    def _check_fixed_wording(
        self,
        drafts: dict[int, WritingSectionDraft],
        nodes: list[WritingOutlineNode],
        clauses: list[WritingClause],
    ) -> list[dict]:
        """检查 fixed_template 内容是否被篡改。

        P1.1: 对每个 fixed_template 节点, 使用 difflib 比对原始 raw_text
        与 draft.content 的相似度。旧实现仅检查 [待补充] 占位符, 与函数
        名"固定措辞"不符, 已补齐真正的 diff 逻辑。
        """
        issues: list[dict] = []
        from app.models.writing_outline_node import OutlineNodeType
        fixed_nodes = [n for n in nodes if n.node_type == OutlineNodeType.fixed_template]
        # 建立 node → clause (fixed_template sub_type) 的映射 (按 node_title 近似匹配)
        fixed_clauses_with_text = [
            c for c in clauses
            if c.sub_type == "fixed_template"
        ]

        for node in fixed_nodes:
            draft = drafts.get(node.id)
            if not draft or not draft.content:
                continue

            if "[待补充" in draft.content:
                issues.append({
                    "type": "fixed_template_incomplete",
                    "severity": "medium",
                    "node_id": node.id,
                    "message": f"固定模板'{node.title}'中有待补充项",
                    "detail": "",
                })

            # 找到与当前章节最相关的 fixed_template 约束, 比对原文与 draft 相似度
            matched_clause = self._match_fixed_clause_for_node(
                node, fixed_clauses_with_text, draft,
            )
            if matched_clause is None:
                issues.append({
                    "type": "fixed_template_source_missing",
                    "severity": "high",
                    "node_id": node.id,
                    "message": f"固定模板'{node.title}'缺少可核对的招标原始模板",
                    "detail": "无法比对生成内容与招标文件模板是否一致，存在废标风险，请人工回查原模板。",
                })
                continue

            source_text, _snapshot = resolve_fixed_template_source(matched_clause)
            structured = validate_fixed_template(
                node_id=node.id,
                node_title=node.title,
                source_text=source_text,
                draft_content=draft.content or "",
            )
            issues.extend(structured.issues)

            raw = self._strip_html(source_text)
            draft_text = self._strip_html(draft.content or "")
            if not raw or not draft_text:
                continue

            ratio = difflib.SequenceMatcher(None, raw, draft_text).ratio()
            if ratio < _FIXED_WORDING_SIMILARITY_THRESHOLD:
                issues.append({
                    "type": "fixed_template_modified",
                    "severity": "high",
                    "node_id": node.id,
                    "message": (
                        f"固定模板'{node.title}'与原始措辞相似度 "
                        f"{ratio:.0%} 低于 {_FIXED_WORDING_SIMILARITY_THRESHOLD:.0%}，"
                        "需人工核对是否与招标模板完全一致"
                    ),
                    "detail": (matched_clause.raw_text or "")[:200],
                })

        return issues

    @staticmethod
    def _strip_html(text: str) -> str:
        """最小化 HTML 去标签, 便于字符级 diff。"""
        if not text:
            return ""
        # 去 <tag ...> / </tag> / <br/>, 合并空白
        cleaned = re.sub(r"<[^>]+>", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _match_fixed_clause_for_node(
        node: WritingOutlineNode,
        fixed_clauses: list[WritingClause],
        draft: Optional[WritingSectionDraft] = None,
    ) -> Optional[WritingClause]:
        """按章节标题关键词与 clause.content 最相近项匹配。"""
        if not fixed_clauses:
            return None
        by_id = {
            clause_id: clause
            for clause in fixed_clauses
            if (clause_id := getattr(clause, "id", None)) is not None
        }
        for clause_id in _parse_clause_ids(getattr(draft, "source_clause_ids", None)):
            clause = by_id.get(clause_id)
            if clause is not None:
                return clause
        title = (node.title or "").strip()
        if not title:
            return None
        for c in fixed_clauses:
            source_text, snapshot = resolve_fixed_template_source(c)
            metadata = getattr(c, "metadata_json", None)
            template_key = None
            source_title = ""
            if isinstance(metadata, dict):
                template_key = metadata.get("template_key")
                source_title = str(metadata.get("target_section_hint") or "")
            if snapshot:
                template_key = snapshot.get("template_key") or template_key
                source_title = str(snapshot.get("title") or source_title)
            if source_title and match_template_title(title, source_title, template_key).matched:
                return c
        # 第一轮: 精确子串匹配
        for c in fixed_clauses:
            source_text, _snapshot = resolve_fixed_template_source(c)
            content = (c.content or "") + " " + source_text
            if title and title in content:
                return c
        # 第二轮: 最长公共短语 (简化版: 基于 title 前 4 字)
        stub = title[:4] if len(title) >= 4 else title
        if not stub:
            return None
        for c in fixed_clauses:
            source_text, _snapshot = resolve_fixed_template_source(c)
            content = (c.content or "") + " " + source_text
            if stub in content:
                return c
        return None

    # ==================== 3. 必填完整性 ====================

    @observe_guard("_check_completeness", "writer")
    def _check_completeness(
        self,
        drafts: dict[int, WritingSectionDraft],
        nodes: list[WritingOutlineNode],
    ) -> list[dict]:
        """检查必填章节是否都有内容"""
        issues: list[dict] = []

        parent_ids = {n.parent_id for n in nodes if n.parent_id is not None}
        leaves = [n for n in nodes if n.id not in parent_ids]

        for node in leaves:
            if node.is_required != 1:
                continue
            draft = drafts.get(node.id)
            if not draft or not draft.content or len(draft.content.strip()) < 20:
                issues.append({
                    "type": "missing_content",
                    "severity": "high",
                    "node_id": node.id,
                    "message": f"必填章节'{node.title}'缺少内容",
                    "detail": "",
                })

        return issues

    # ==================== 4. 交叉引用检查 ====================

    @observe_guard("_check_cross_references", "writer")
    async def _check_cross_references(
        self,
        drafts: dict[int, WritingSectionDraft],
        nodes: list[WritingOutlineNode],
    ) -> list[dict]:
        """检查"详见第X章"等交叉引用是否正确。

        P1.1: 扩展正则覆盖 见/详见/参见/参照/如上/附录 + 章/节/部分/条/附件;
        LLM 失败按错误类型分级 (网络/超时 → warn, 解析异常 → reject)。
        """
        node_by_id = {node.id: node for node in nodes}
        outline_rows = [
            f"- [{index}] {node.title}"
            for index, node in enumerate(nodes, start=1)
        ]
        node_order = {node.id: index for index, node in enumerate(nodes, start=1)}
        reference_records: list[str] = []
        seen_records: set[tuple[int | None, str, str]] = set()
        for draft in drafts.values():
            if not draft.content:
                continue
            source_node = node_by_id.get(draft.outline_node_id)
            source_title = source_node.title if source_node else "(未知章节)"
            source_order = node_order.get(draft.outline_node_id, 0)
            content = draft.content or ""
            for match in _CROSS_REF_PATTERN.finditer(content):
                frag = match.group(1) or match.group(2) or ""
                frag = (frag or "").strip()
                if not frag:
                    continue
                start = max(match.start() - 100, 0)
                end = min(match.end() + 100, len(content))
                context = self._strip_html(content[start:end])[:240]
                dedupe_key = (draft.outline_node_id, frag, context)
                if dedupe_key in seen_records:
                    continue
                seen_records.add(dedupe_key)
                reference_records.append(
                    "\n".join([
                        f"- fragment: {frag}",
                        f"  source_section: [{source_order}] {source_title}",
                        f"  context: {context}",
                    ])
                )

        if not reference_records:
            return []

        outline_text = "\n".join(outline_rows)
        fragments_text = "\n\n".join(reference_records)

        system_msg, user_msg = get_cross_ref_check_prompt(outline_text, fragments_text)
        temperature, max_tokens = resolve_runtime_params(
            "writing.cross_ref_check", 0.1, 2000,
        )

        try:
            result = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            cross_reference_check_failure_total.labels(
                flow="writer", reason=type(e).__name__,
            ).inc()
            severity, failure_reason = self._classify_cross_ref_failure(e)
            logger.warning(f"交叉引用检查失败(降级, severity={severity}): {e}")
            try:
                from app.core.observability.degradation_aggregator import (
                    record_degradation as _record_degradation,
                )
                _record_degradation(
                    kind="cross_reference_llm_failed",
                    ref="cross_ref",
                    reason=f"{type(e).__name__}: {str(e)[:120]}",
                )
            except Exception:
                pass
            return [{
                "type": "cross_reference_check_degraded",
                "severity": severity,
                "node_id": None,
                "message": "交叉引用 LLM 检查异常，已降级",
                "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "failure_state": FailureState.DEGRADED.value,
                "failure_reason": failure_reason,
            }]

        issues: list[dict] = []
        raw_list = result if isinstance(result, list) else []
        for item in raw_list:
            if isinstance(item, dict) and not item.get("is_correct", True):
                issues.append({
                    "type": "cross_reference_error",
                    "severity": "medium",
                    "node_id": None,
                    "message": f"交叉引用错误: '{item.get('fragment', '')}'",
                    "detail": item.get("suggestion", ""),
                })

        return issues

    @staticmethod
    def _classify_cross_ref_failure(exc: BaseException) -> tuple[str, str]:
        """P1.1: 根据异常类型对交叉引用 LLM 失败分级。

        - 超时/网络错误: severity=low, 视为临时不可用, 不阻断用户;
        - 结果解析/业务异常: severity=medium, 提示可能遗漏真实引用错误。
        """
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        network_markers = (
            "timeout", "timed out", "connection", "connect",
            "network", "econnreset", "readtimeout", "writetimeout",
        )
        if any(m in name for m in network_markers) or any(
            m in message for m in network_markers
        ):
            return "low", "cross_reference_llm_network_error"
        return "medium", "cross_reference_llm_result_error"

    # ==================== 5. 术语一致性 ====================

    @observe_guard("_check_terminology", "writer")
    def _check_terminology(
        self,
        drafts: dict[int, WritingSectionDraft],
        profile_terms: Optional[list[str]] = None,
    ) -> list[dict]:
        """检查同一实体与行业术语在全文中的写法是否统一

        Args:
            drafts: 各章节当前草稿
            profile_terms: 行业画像术语表，缺省时只做称谓一致性检查
        """
        issues: list[dict] = []

        common_variants = [
            (["甲方", "采购人", "招标人", "业主"], "招标方称谓"),
            (["乙方", "投标人", "供应商", "承包商", "中标人"], "投标方称谓"),
        ]

        full_text = " ".join(d.content or "" for d in drafts.values())

        for variants, category in common_variants:
            found = [v for v in variants if v in full_text]
            if len(found) > 1:
                issues.append({
                    "type": "terminology_inconsistency",
                    "severity": "low",
                    "node_id": None,
                    "message": f"{category}不统一: 同时使用了 {', '.join(found)}",
                    "detail": "建议全文统一使用同一称谓",
                })

        issues.extend(find_term_variant_issues(full_text, profile_terms or []))

        return issues

    @staticmethod
    def _find_industry_profile_content(
        clauses: list[WritingClause],
    ) -> Optional[str]:
        """从约束集合中取出行业画像正文，缺失返回 None。"""
        for clause in clauses:
            if clause.sub_type == "industry_profile":
                return clause.content
        return None

    # ==================== DB 读取 ====================

    async def _load_all_current_drafts(
        self, project_id: int
    ) -> dict[int, WritingSectionDraft]:
        """加载全部当前版本草稿，返回 {node_id: draft}"""
        result = await self.db.execute(
            select(WritingSectionDraft).join(
                WritingOutlineNode,
                WritingSectionDraft.outline_node_id == WritingOutlineNode.id,
            ).where(
                WritingOutlineNode.project_id == project_id,
                WritingSectionDraft.is_current == 1,
            )
        )
        drafts = list(result.scalars().all())
        return {d.outline_node_id: d for d in drafts}

    async def _load_all_nodes(
        self, project_id: int
    ) -> list[WritingOutlineNode]:
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        return list(result.scalars().all())

    async def _load_hard_constraints(
        self, project_id: int
    ) -> list[WritingClause]:
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.layer == WritingClauseLayer.hard_constraint,
            )
        )
        return list(result.scalars().all())

    async def _load_all_clauses(self, project_id: int) -> list[WritingClause]:
        result = await self.db.execute(
            select(WritingClause).where(WritingClause.project_id == project_id)
        )
        return list(result.scalars().all())


def get_consistency_checker(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> ConsistencyChecker:
    """工厂函数"""
    return ConsistencyChecker(db=db, llm_client=llm_client, project_id=project_id)


def _parse_clause_ids(raw: object) -> list[int]:
    """解析 draft.source_clause_ids。"""
    if not raw:
        return []
    try:
        values = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [int(value) for value in values if isinstance(value, int) or str(value).isdigit()]
