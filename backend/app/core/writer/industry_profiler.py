"""行业画像生成器

基于已提取的三层约束，调用 LLM 生成行业写作画像。
画像存为一条特殊的 writing_clause (layer=generation_guide, sub_type=industry_profile)。
"""

import re
import json
from typing import Callable, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import get_review_client, LLMClient
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.writer.text_budget import shrink_semantic_text as _shrink_semantic_text
from app.core.writer.industry_profile_gate import fulfill_industry_profile_text
from app.core.writer.prompts.industry_profile import (
    INDUSTRY_PROFILE_COMPLETION_SYSTEM,
    attach_output_contract,
    get_industry_profile_prompt as _code_industry_profile,
)
from app.core.ai.prompt_registry import get_registry as _get_reg
from app.core.ai.prompt_registry import resolve_runtime_params


def get_industry_profile_prompt(*a, **kw):
    """Registry 优先加载行业画像 Prompt，命中时对 user_template 做变量注入。"""
    r = _get_reg().get_prompt("writing.industry_profile")
    if r.system_message and r.user_template:
        fmt_vars = dict(kw)
        if len(a) >= 1:
            fmt_vars.setdefault("project_context", a[0])
        if len(a) >= 2:
            fmt_vars.setdefault("scoring_summary", a[1])
        if len(a) >= 3:
            fmt_vars.setdefault("content_requirements", a[2])
        if len(a) >= 4:
            fmt_vars.setdefault("material_requirements", a[3])
        if len(a) >= 5:
            fmt_vars.setdefault("differentiation_hints", a[4])
        if len(a) >= 6:
            fmt_vars.setdefault("directory_rules", a[5])
        if len(a) >= 7:
            fmt_vars.setdefault("structured_signals", a[6])
        try:
            user_template = r.user_template
            extra_sections = []
            if "{differentiation_hints}" not in user_template and fmt_vars.get("differentiation_hints"):
                extra_sections.append("### 差异化提示\n{differentiation_hints}")
            if "{directory_rules}" not in user_template and fmt_vars.get("directory_rules"):
                extra_sections.append("### 目录/结构规则\n{directory_rules}")
            if "{structured_signals}" not in user_template and fmt_vars.get("structured_signals"):
                extra_sections.append("### 结构化信号摘要\n{structured_signals}")
            if extra_sections:
                user_template += "\n\n" + "\n\n".join(extra_sections)
            formatted_user = user_template.format(**fmt_vars)
            logger.debug(
                f"PromptRegistry hit: node=writing.industry_profile, "
                f"formatted_len={len(formatted_user)}"
            )
            return attach_output_contract(r.system_message, formatted_user)
        except (KeyError, IndexError) as e:
            logger.warning(
                f"PromptRegistry user_template 格式化失败, "
                f"node=writing.industry_profile, error={e}, 回退代码默认 Prompt"
            )
    return _code_industry_profile(*a, **kw)
from app.models.writing_clause import WritingClause, WritingClauseLayer


_PROFILE_LEADING_PHRASES = (
    "好的，",
    "好的,",
    "作为行业分析专家",
    "作为资深投标顾问",
    "我已根据",
    "以下为您生成",
    "下面是",
)
_PROFILE_BODY_START_RE = re.compile(
    r"(###\s*1\.\s*行业识别与典型特征|"
    r"1\.\s*行业识别与典型特征|"
    r"一、行业识别与典型特征)"
)
_INSUFFICIENT_PROFILE_TEXT = (
    "参考信息不足，无法生成可靠行业画像。"
    "请先完成约束提取或补充有效招标信息。"
)


def _shrink_profile_payload(
    payload: dict[str, str],
    retry_round: int,
) -> tuple[dict[str, str], dict[str, object]]:
    """输入超长时按固定优先级收缩画像上下文。"""
    reduced = dict(payload)
    removed_segments: list[str] = []
    shrink_order = (
        "material_requirements",
        "content_requirements",
        "scoring_summary",
        "structured_signals",
        "directory_rules",
        "differentiation_hints",
        "project_context",
    )
    for key in shrink_order:
        if reduced.get(key):
            reduced[key] = _shrink_semantic_text(reduced[key], min_lines=1)
            removed_segments.append(key)
            break
    return reduced, {
        "removed_segments": removed_segments,
        "kept_segments": ["industry_profile_prompt_contract"],
        "retry_attempt": retry_round,
    }


async def request_profile_with_contract(
    llm_client: LLMClient,
    context: dict[str, str],
    *,
    project_id: int,
    progress_callback: Optional[Callable[[str], None]],
    sanitize: Callable[[str], str],
) -> tuple[str, dict[str, str] | None]:
    """一次生成 + 必要时一次续写，返回正文与可选闸元数据。"""
    if progress_callback:
        progress_callback("等待行业画像模型返回")

    async def _send_request(payload: dict[str, str]):
        system_msg, user_msg = get_industry_profile_prompt(**payload)
        temperature, max_tokens = resolve_runtime_params(
            "writing.industry_profile", 0.2, 4000,
        )
        return await llm_client.chat(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    profile_text = await retry_on_input_too_long_400(
        operation_name=f"标书行业画像(project={project_id})",
        initial_payload=context,
        send_request=_send_request,
        reduce_payload_semantically=_shrink_profile_payload,
        max_retries=1,
        describe_payload=lambda payload: {
            key: len(value)
            for key, value in payload.items()
            if isinstance(value, str)
        },
    )
    profile_text = sanitize(profile_text)
    if progress_callback:
        progress_callback("校验行业画像结构")
    temperature, max_tokens = resolve_runtime_params(
        "writing.industry_profile", 0.2, 4000,
    )

    async def _complete_profile(user_msg: str) -> str:
        return await llm_client.chat(
            user_message=user_msg,
            system_message=INDUSTRY_PROFILE_COMPLETION_SYSTEM,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    profile_text, gate_meta = await fulfill_industry_profile_text(
        profile_text,
        sanitize=sanitize,
        complete=_complete_profile,
    )
    return profile_text, gate_meta or None


def _metadata_summary(clause: WritingClause) -> str:
    metadata = getattr(clause, "metadata_json", None)
    if not isinstance(metadata, dict) or not metadata:
        return ""
    keys = [
        "target_section_hint", "evidence_hint", "score_item_name", "max_score",
        "material_kind", "required_form", "applies_to", "is_mandatory",
    ]
    compact = {
        key: metadata.get(key)
        for key in keys
        if metadata.get(key) not in (None, "", [], {})
    }
    if not compact:
        return ""
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


class IndustryProfiler:
    """基于招标文件动态生成行业写作画像"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)

    async def generate(
        self,
        project_id: int,
        document_id: int,
        clauses: list[WritingClause],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> WritingClause:
        """生成行业画像并存入 DB

        Args:
            project_id: 项目 ID
            document_id: 招标文件 document ID
            clauses: 已提取的全部约束

        Returns:
            行业画像 WritingClause 记录
        """
        if progress_callback:
            progress_callback("整理行业画像上下文")
        context = self._build_context(clauses)
        profile_metadata: dict[str, str] | None = None

        if not self._has_profile_evidence(context):
            logger.warning(
                f"行业画像证据不足，跳过模型生成: project={project_id}"
            )
            if progress_callback:
                progress_callback("参考信息不足，跳过行业画像模型调用")
            profile_text = _INSUFFICIENT_PROFILE_TEXT
            profile_metadata = {
                "profile_confidence": "insufficient",
                "source": "evidence_gate",
                "reason": "no_extracted_context",
            }
        else:
            try:
                profile_text, profile_metadata = await request_profile_with_contract(
                    self.llm_client,
                    context,
                    project_id=project_id,
                    progress_callback=progress_callback,
                    sanitize=self._sanitize_profile_text,
                )
            except Exception as e:
                logger.error(f"行业画像生成失败: {e}")
                profile_text = "（行业画像生成失败，请手动补充）"

        clause = WritingClause(
            project_id=project_id,
            document_id=document_id,
            layer=WritingClauseLayer.generation_guide,
            sub_type="industry_profile",
            content=self._sanitize_profile_text(profile_text),
            raw_text=None,
            source_page=None,
            score_weight=None,
            metadata_json=profile_metadata,
            confirmed=0,
            sort_order=9999,
        )
        if progress_callback:
            progress_callback("保存行业画像结果")
        self.db.add(clause)
        await self.db.commit()
        await self.db.refresh(clause)

        logger.info(
            f"行业画像已生成: project={project_id}, "
            f"length={len(clause.content)} chars"
        )
        return clause

    def _build_context(self, clauses: list[WritingClause]) -> dict[str, str]:
        """从约束列表中构建行业画像所需的上下文摘要"""
        project_context_parts: list[str] = []
        scoring_parts: list[str] = []
        content_req_parts: list[str] = []
        material_req_parts: list[str] = []
        differentiation_parts: list[str] = []
        directory_rule_parts: list[str] = []
        structured_signal_parts: list[str] = []

        for c in clauses:
            if c.sub_type == "project_context":
                project_context_parts.append(f"- {c.content}")
            elif c.sub_type == "scoring_criterion":
                weight = f"({c.score_weight}分)" if c.score_weight else ""
                scoring_parts.append(f"- {c.content} {weight}")
            elif c.sub_type == "content_required":
                content_req_parts.append(f"- {c.content}")
            elif c.sub_type == "emphasis_point":
                content_req_parts.append(f"- [重点] {c.content}")
            elif c.sub_type == "material_required":
                material_req_parts.append(f"- {c.content}")
            elif c.sub_type == "differentiation_hint":
                differentiation_parts.append(f"- {c.content}")
            elif c.sub_type == "directory_rule":
                directory_rule_parts.append(f"- {c.content}")

            metadata = _metadata_summary(c)
            if metadata:
                structured_signal_parts.append(
                    f"- {c.sub_type}: {metadata}"
                )

        return {
            "project_context": "\n".join(project_context_parts),
            "scoring_summary": "\n".join(scoring_parts),
            "content_requirements": "\n".join(content_req_parts),
            "material_requirements": "\n".join(material_req_parts),
            "differentiation_hints": "\n".join(differentiation_parts),
            "directory_rules": "\n".join(directory_rule_parts),
            "structured_signals": "\n".join(structured_signal_parts),
        }

    @staticmethod
    def _has_profile_evidence(context: dict[str, str]) -> bool:
        """判断行业画像是否具备可支撑生成的提取证据。"""
        return any((value or "").strip() for value in context.values())

    @classmethod
    def _sanitize_profile_text(cls, text: str) -> str:
        """移除行业画像前导套话，保留可直接展示的正文。"""
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned

        body_start = _PROFILE_BODY_START_RE.search(cleaned)
        if body_start:
            cleaned = cleaned[body_start.start():].strip()
            return cleaned or (text or "").strip()

        lines = cleaned.splitlines()
        trimmed_lines = []
        skipping = True
        for line in lines:
            stripped = line.strip()
            if (
                skipping
                and stripped
                and any(stripped.startswith(prefix) for prefix in _PROFILE_LEADING_PHRASES)
            ):
                continue
            if stripped:
                skipping = False
            trimmed_lines.append(line)

        cleaned = "\n".join(trimmed_lines).strip()
        return cleaned or (text or "").strip()


def get_industry_profiler(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> IndustryProfiler:
    """工厂函数"""
    return IndustryProfiler(db=db, llm_client=llm_client, project_id=project_id)
