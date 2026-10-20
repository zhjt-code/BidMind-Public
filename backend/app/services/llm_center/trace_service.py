"""TraceService（R2-14 拆分产物）

LLM Center 调试运行 + 执行轨迹 + 追踪模式 + 发布历史 + 归档。

公共方法：
- test_run: 调试运行（使用指定参数执行单次 LLM 调用， audit）
- query_execution_traces: 查询执行轨迹（支持 node_key / project_id 过滤）
- set_trace_mode: 切换追踪模式（aggregate / full）
- query_publish_history: 查询节点发布历史
- archive_old_traces: 归档 N 天前的 full 模式明细记录
- record_execution: 记录一次 LLM 执行（聚合模式累加）
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from loguru import logger
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_template_version import LlmTemplateVersion
from app.models.llm_execution_trace import LlmExecutionTrace
from app.models.llm_publish_record import LlmPublishRecord

from app.services.llm_center._helpers import _get_node_or_raise


class TraceService:
    """LLM Center 调试运行 + 执行轨迹观测服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def test_run(self, node_key: str, param_values: dict, version_id: int | None = None, username: str = "") -> dict:
        """调试运行：使用指定参数执行单次 LLM 调用"""
        logger.info(f"[LLM Center] test_run: {node_key} version_id={version_id} (by {username})")
        node = await _get_node_or_raise(self.db, node_key)

        if version_id:
            version = (await self.db.execute(
                select(LlmTemplateVersion).where(LlmTemplateVersion.id == version_id)
            )).scalar_one_or_none()
        else:
            version = (await self.db.execute(
                select(LlmTemplateVersion).where(
                    LlmTemplateVersion.node_id == node.id,
                    LlmTemplateVersion.is_active == True,
                    LlmTemplateVersion.template_role == "system",
                )
            )).scalar_one_or_none()

        if not version:
            return {"success": False, "error": "无可用的 Prompt 版本", "raw_output": "", "latency_ms": 0, "model_name": "", "token_usage": {}}

        user_version = (await self.db.execute(
            select(LlmTemplateVersion).where(
                LlmTemplateVersion.node_id == node.id,
                LlmTemplateVersion.is_active == True,
                LlmTemplateVersion.template_role == "user",
            )
        )).scalar_one_or_none()

        system_msg = version.content
        try:
            user_msg = user_version.content.format(**param_values) if user_version else str(param_values)
        except KeyError as e:
            return {
                "success": False,
                "error": f"缺少必填参数: {e}。请在 param_values 中提供模板所需的全部变量。",
                "raw_output": "", "latency_ms": 0, "model_name": "", "token_usage": {},
            }

        try:
            from app.core.ai.ai_factory import get_review_client
            from app.core.ai.attempt_recorder import current_traffic_class
            from app.core.ai.prompt_registry import current_node_key
            client = get_review_client()
            start = time.monotonic()
            _tc_token = current_traffic_class.set("admin_test")
            _nk_token = current_node_key.set(node_key)
            try:
                raw = await client.chat(
                    user_message=user_msg,
                    system_message=system_msg,
                    temperature=node.default_temperature,
                    max_tokens=node.default_max_tokens,
                )
            finally:
                current_node_key.reset(_nk_token)
                current_traffic_class.reset(_tc_token)
            latency_ms = (time.monotonic() - start) * 1000
            logger.info(f"[LLM Center] test_run OK: {node_key} latency={round(latency_ms, 1)}ms")
            from app.services.audit_service import audit_log
            await audit_log(
                self.db, username=username, category="llm_center",
                action="test_run", resource_type="llm_node",
                detail=f"调试运行 {node_key} 成功, {round(latency_ms, 1)}ms",
            )
            return {
                "success": True, "raw_output": raw,
                "latency_ms": round(latency_ms, 1),
                "model_name": getattr(client, 'model_name', 'unknown'),
                "token_usage": {}, "parsed_output": None, "error": None,
            }
        except Exception as e:
            logger.warning(f"[LLM Center] test_run FAIL: {node_key} error={e}")
            from app.services.audit_service import audit_log
            await audit_log(
                self.db, username=username, category="llm_center",
                action="test_run", resource_type="llm_node",
                detail=f"调试运行 {node_key} 失败: {str(e)[:200]}",
            )
            return {
                "success": False, "error": str(e),
                "raw_output": "", "latency_ms": 0,
                "model_name": "", "token_usage": {},
            }

    async def query_execution_traces(
        self, node_key: str | None = None, project_id: int | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """查询执行轨迹"""
        from app.schemas.llm_center import ExecutionTraceInfo
        q = select(LlmExecutionTrace).order_by(LlmExecutionTrace.created_at.desc())
        if node_key:
            q = q.where(LlmExecutionTrace.node_key == node_key)
        if project_id:
            q = q.where(LlmExecutionTrace.project_id == project_id)
        q = q.limit(limit).offset(offset)
        traces = (await self.db.execute(q)).scalars().all()
        return [ExecutionTraceInfo(
            id=t.id, project_id=t.project_id, node_key=t.node_key,
            template_version_id=t.template_version_id,
            execution_channel=t.execution_channel, model_name=t.model_name,
            temperature=t.temperature, max_tokens=t.max_tokens,
            trace_mode=t.trace_mode, call_count=t.call_count,
            total_latency_ms=t.total_latency_ms,
            total_prompt_tokens=t.total_prompt_tokens,
            total_completion_tokens=t.total_completion_tokens,
            error_count=t.error_count, retry_count=t.retry_count,
            created_at=t.created_at, updated_at=t.updated_at,
        ).model_dump() for t in traces]

    async def set_trace_mode(self, node_key: str, trace_mode: str) -> dict:
        """切换追踪模式"""
        if trace_mode not in ("aggregate", "full"):
            from app.core.exceptions import ServiceError
            raise ServiceError(f"无效的追踪模式: {trace_mode}")
        return {"node_key": node_key, "trace_mode": trace_mode, "message": "追踪模式已设置"}

    async def query_publish_history(self, node_key: str) -> list[dict]:
        """查询节点发布历史"""
        node = await _get_node_or_raise(self.db, node_key)
        records = (await self.db.execute(
            select(LlmPublishRecord)
            .where(LlmPublishRecord.node_id == node.id)
            .order_by(LlmPublishRecord.created_at.desc())
        )).scalars().all()
        return [{
            "id": r.id,
            "action": r.action,
            "published_by": r.published_by,
            "template_version_id": r.template_version_id,
            "previous_version_id": r.previous_version_id,
            "change_note": r.change_note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in records]

    async def archive_old_traces(self, days: int = 30) -> int:
        """归档 N 天前的 full 模式明细记录"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.db.execute(
            sa_delete(LlmExecutionTrace).where(
                LlmExecutionTrace.trace_mode == "full",
                LlmExecutionTrace.created_at < cutoff,
            )
        )
        await self.db.flush()
        return result.rowcount

    async def record_execution(
        self, node_key: str, project_id: int | None, latency_ms: float,
        prompt_tokens: int = 0, completion_tokens: int = 0,
        error: bool = False, model_name: str = "",
        execution_channel: str = "review",
    ) -> None:
        """记录一次 LLM 执行（聚合模式）"""
        existing = (await self.db.execute(
            select(LlmExecutionTrace).where(
                LlmExecutionTrace.node_key == node_key,
                LlmExecutionTrace.project_id == project_id,
                LlmExecutionTrace.trace_mode == "aggregate",
            )
        )).scalar_one_or_none()

        if existing:
            existing.call_count += 1
            existing.total_latency_ms += latency_ms
            existing.total_prompt_tokens += prompt_tokens
            existing.total_completion_tokens += completion_tokens
            if error:
                existing.error_count += 1
            existing.model_name = model_name or existing.model_name
        else:
            self.db.add(LlmExecutionTrace(
                node_key=node_key, project_id=project_id,
                execution_channel=execution_channel,
                model_name=model_name, trace_mode="aggregate",
                call_count=1, total_latency_ms=latency_ms,
                total_prompt_tokens=prompt_tokens,
                total_completion_tokens=completion_tokens,
                error_count=1 if error else 0,
            ))
        await self.db.flush()
