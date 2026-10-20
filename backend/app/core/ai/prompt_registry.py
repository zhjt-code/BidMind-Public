"""PromptRegistry -- 运行时 Prompt 的唯一真相源

三级加载优先级: DB 激活版本 > Seed 初始数据 > 代码默认值

职责:
1. 从 DB 加载全部激活版本到内存缓存
2. 为业务代码提供统一的 get_prompt(node_key) 接口
3. 对未注册的 node_key 做 graceful fallback (代码默认值 + WARNING)
4. 前端发布时热更新缓存
5. registry_sync: 启动时代码 -> DB 同步
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 协程级上下文：业务调用 get_prompt() 后，dispatcher 据此记录轨迹
current_node_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_node_key", default=None,
)
current_project_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_project_id", default=None,
)
# 任务级 Prompt 版本快照 (dict[node_key -> (system_version_id, user_version_id)])
# 由 worker 各 job 入口通过 install_task_snapshot() 绑定, get_prompt() 自动消费。
# 保证任务中途 publish 新版本也不会让已启动任务的 prompt 漂移。
current_snapshot: contextvars.ContextVar[
    Optional[dict[str, tuple[Optional[int], Optional[int]]]]
] = contextvars.ContextVar("current_snapshot", default=None)
from sqlalchemy.orm import selectinload


# ═══════════════════════════════════════════════
# 跨进程热更新 -- Redis Pub/Sub Channel 与消息协议
# ═══════════════════════════════════════════════

# 发布 Prompt 变更时广播的 channel;
# worker/API 订阅该 channel, 收到事件即 refresh 本地缓存。
INVALIDATE_CHANNEL = "bidmind:prompt_registry:invalidate"

# 消息格式:
#   {"type": "refresh", "node_key": "bid_review.review_item"}  单节点刷新
#   {"type": "reload"}                                         全量重载
_INVALIDATE_TYPE_REFRESH = "refresh"
_INVALIDATE_TYPE_RELOAD = "reload"


@dataclass
class ResolvedPrompt:
    """解析后的 Prompt，包含 system/user 模板和运行时配置"""
    node_key: str
    system_message: str = ""
    user_template: str = ""
    temperature: float = 0.1
    max_tokens: int = 2000
    system_version_id: Optional[int] = None
    user_version_id: Optional[int] = None
    # S.3.2: 稳定性等级。deterministic 类节点的温度会被强制 0.0；其他类保持原温度
    stability_class: str = "semi"


@dataclass
class NodeMeta:
    """节点元数据"""
    node_key: str
    name: str
    builder_key: str = ""
    source_file: str = ""
    execution_channel: str = "review"
    call_method: str = "chat_json"
    output_schema_name: str = ""
    visibility: str = "main"
    editable_level: str = "advanced"
    node_class: str = "judgment"


@dataclass
class _CachedNode:
    """内存缓存条目"""
    node_id: int
    meta: NodeMeta
    system_text: str = ""
    user_text: str = ""
    system_version_id: Optional[int] = None
    user_version_id: Optional[int] = None
    temperature: float = 0.1
    max_tokens: int = 2000
    # S.3.2: 稳定性等级（deterministic/semi/creative），load() 阶段按 node_key 推导
    stability_class: str = "semi"


# S.3.2: 稳定性等级推导。命中 deterministic → 强制 temperature=0.0；creative → 保持原值但允许更高温度；
# 其他 → semi（原温度不变）。规则按 node_key 前缀匹配，与 seed_llm_registry.py 的命名保持一致。
_DETERMINISTIC_PREFIXES: tuple[str, ...] = (
    # 投标审查：提取/审查/合并/图片识别，全部要求事实性判定
    "bid_review.",
    # 串标审查：所有维度对比、提取、判定、引用回查均为事实类
    "collusion.tender_format",
    "collusion.route_extract",
    "collusion.extraction",
    "collusion.pair_compare",
    "collusion.single_dim_search",
    "collusion.pair_judgement",
    "collusion.group_analysis",
    "collusion.project_summary",
    "collusion.overall_synthesis",
    "collusion.image_recognition",
    # 写标：约束提取类、分类类、匹配裁决类均为事实判定
    "writing.hard_clause",
    "writing.structure_clause",
    "writing.guide_clause",
    "writing.unified_clause",
    "writing.render_config",
    "writing.clause_dedup",
    "writing.clause_classify",
    "writing.relevance_classify",
    "writing.bid_structure_analysis",
    "writing.bid_directory_extract",
    "writing.material_tag",
    "writing.image_classify",
    "writing.chapter_judge",
    "writing.chapter_profile",  # 章节画像 expects/rejects 为事实分类任务, 影响 matcher 裁决稳定性
    "writing.section_plan",
    "writing.material_pair_judge",  # 素材句对蕴含裁决为事实判定, 强制 temperature=0.0 保绑定稳定
    "writing.cross_ref_check",
    # Critic 层（Judge / CoVe）
    "judge.",
    "cove.",
    # 文档质检复核: 候选真伪是事实判定
    "document_qa.",
)

_CREATIVE_PREFIXES: tuple[str, ...] = (
    # 写标：自由论述 / 摘要 / 骨架 / 章节填充 / 行业画像均为创作类
    "writing.free_writing",
    "writing.section_summary",
    "writing.industry_profile",
    "writing.skeleton",
    "writing.section_fill",
    "writing.outline",
    "writing.data_fill",
)


def _derive_stability_class(node_key: str) -> str:
    """按 node_key 前缀推导 stability_class。"""
    if not node_key:
        return "semi"
    nk = str(node_key)
    for p in _DETERMINISTIC_PREFIXES:
        if nk.startswith(p):
            return "deterministic"
    for p in _CREATIVE_PREFIXES:
        if nk.startswith(p):
            return "creative"
    return "semi"


def _apply_stability_class_temperature(
    base_temperature: float,
    stability_class: str,
) -> float:
    """按 stability_class 覆盖基础温度。deterministic 强制 0.0，其他保持原值。"""
    if stability_class == "deterministic":
        return 0.0
    return base_temperature


class PromptRegistry:
    """Prompt 注册表 -- 单例"""

    def __init__(self) -> None:
        self._cache: dict[str, _CachedNode] = {}
        self._loaded = False
        self._lock = asyncio.Lock()
        self._pending_traces: list[asyncio.Task] = []
        # 同一 node_key 的"未注册"告警只在首次出现时 WARNING, 后续降级为 DEBUG,
        # 避免单进程内缺失节点在高并发调用链上刷屏 (典型场景: 审查链按项反复取 prompt)。
        # 当 load/refresh 补录了该节点后, 会从集合中移除, 允许二次 WARNING 再次出现。
        self._missing_node_logged: set[str] = set()
        # 任务快照二级缓存: (node_key, version_id, role) -> template_text
        # 由 resolve_snapshot() 在任务启动时预热, 后续 cache 被 refresh 刷新也不会丢。
        # 保证任务中途 prompt 热更新后, 已启动任务仍用绑定时的版本。
        self._version_cache: dict[tuple[str, int, str], str] = {}
        # 跨进程订阅协程句柄; start_invalidate_subscriber/stop_invalidate_subscriber 使用。
        self._invalidate_sub_task: Optional[asyncio.Task] = None
        self._invalidate_sub_stop: Optional[asyncio.Event] = None
        self._invalidate_sub_client: Any = None
        self._invalidate_sub_owns_client = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ──────────────────────────────────────────
    # 加载 / 刷新
    # ──────────────────────────────────────────

    async def load(self, db: AsyncSession) -> None:
        """从 DB 加载全部激活版本到内存缓存"""
        from app.models.llm_node import LlmNode
        from app.models.llm_template_version import LlmTemplateVersion

        async with self._lock:
            nodes = (await db.execute(
                select(LlmNode).where(LlmNode.is_active == True)
            )).scalars().all()

            new_cache: dict[str, _CachedNode] = {}
            for node in nodes:
                meta = NodeMeta(
                    node_key=node.node_key, name=node.name,
                    builder_key=node.builder_key or "",
                    source_file=node.source_file or "",
                    execution_channel=node.execution_channel,
                    call_method=node.call_method,
                    output_schema_name=node.output_schema_name or "",
                    visibility=node.visibility,
                    editable_level=node.editable_level,
                    node_class=getattr(node, "node_class", None) or "judgment",
                )

                versions = (await db.execute(
                    select(LlmTemplateVersion).where(
                        LlmTemplateVersion.node_id == node.id,
                        LlmTemplateVersion.is_active == True,
                    )
                )).scalars().all()

                _sc = _derive_stability_class(node.node_key)
                entry = _CachedNode(
                    node_id=node.id, meta=meta,
                    temperature=node.default_temperature,
                    max_tokens=node.default_max_tokens,
                    stability_class=_sc,
                )
                for v in versions:
                    if v.template_role == "system":
                        entry.system_text = v.content
                        entry.system_version_id = v.id
                    elif v.template_role == "user":
                        entry.user_text = v.content
                        entry.user_version_id = v.id

                new_cache[node.node_key] = entry

            self._cache = new_cache
            self._loaded = True
            # 已加载的节点从 missing 集合中移除, 允许后续再次变为缺失时重新告警
            self._missing_node_logged.difference_update(new_cache.keys())
            logger.info(f"PromptRegistry 已加载 {len(new_cache)} 个节点")

    async def refresh(self, db: AsyncSession, node_key: Optional[str] = None) -> None:
        """刷新缓存（指定节点或全部）"""
        if node_key is None:
            await self.load(db)
            return

        from app.models.llm_node import LlmNode
        from app.models.llm_template_version import LlmTemplateVersion

        async with self._lock:
            node = (await db.execute(
                select(LlmNode).where(LlmNode.node_key == node_key)
            )).scalar_one_or_none()
            if not node:
                self._cache.pop(node_key, None)
                return

            versions = (await db.execute(
                select(LlmTemplateVersion).where(
                    LlmTemplateVersion.node_id == node.id,
                    LlmTemplateVersion.is_active == True,
                )
            )).scalars().all()

            meta = NodeMeta(
                node_key=node.node_key, name=node.name,
                builder_key=node.builder_key or "",
                source_file=node.source_file or "",
                execution_channel=node.execution_channel,
                call_method=node.call_method,
                output_schema_name=node.output_schema_name or "",
                visibility=node.visibility,
                editable_level=node.editable_level,
                node_class=getattr(node, "node_class", None) or "judgment",
            )
            entry = _CachedNode(
                node_id=node.id, meta=meta,
                temperature=node.default_temperature,
                max_tokens=node.default_max_tokens,
                stability_class=_derive_stability_class(node.node_key),
            )
            for v in versions:
                if v.template_role == "system":
                    entry.system_text = v.content
                    entry.system_version_id = v.id
                elif v.template_role == "user":
                    entry.user_text = v.content
                    entry.user_version_id = v.id

            self._cache[node_key] = entry
            self._missing_node_logged.discard(node_key)

    # ──────────────────────────────────────────
    # 读取接口
    # ──────────────────────────────────────────

    def get_prompt(
        self,
        node_key: str,
        snapshot_version_id: Optional[int] = None,
        snapshot: Optional[tuple[Optional[int], Optional[int]]] = None,
    ) -> ResolvedPrompt:
        """获取节点的最终 Prompt

        参数优先级:
            1. 显式 snapshot tuple (system_version_id, user_version_id)
            2. 显式 snapshot_version_id (int, 仅作为 system_version_id 兼容旧签名)
            3. current_snapshot contextvar (任务启动时由 install_task_snapshot 绑定)
            4. 当前激活版本 (_cache)

        未注册的 node_key 返回空 ResolvedPrompt, 首次 WARNING、后续 DEBUG
        （业务代码应 fallback 到代码默认值）。
        """
        current_node_key.set(node_key)

        # 合并 snapshot 参数 (显式 > contextvar)
        effective_snap: Optional[tuple[Optional[int], Optional[int]]] = None
        if snapshot is not None:
            effective_snap = snapshot
        elif snapshot_version_id is not None:
            effective_snap = (snapshot_version_id, None)
        else:
            ctx_snap = current_snapshot.get()
            if ctx_snap is not None:
                effective_snap = ctx_snap.get(node_key)

        # 任务快照命中: 尝试用版本二级缓存取模板
        if effective_snap is not None:
            snap_sys_id, snap_user_id = effective_snap
            snap_sys_text = (
                self._version_cache.get((node_key, snap_sys_id, "system"))
                if snap_sys_id
                else None
            )
            snap_user_text = (
                self._version_cache.get((node_key, snap_user_id, "user"))
                if snap_user_id
                else None
            )
            entry = self._cache.get(node_key)
            # 任一侧命中 snapshot 即视为受控返回; 未命中侧用当前 cache 兜底 (保持原行为不降级)
            if snap_sys_text is not None or snap_user_text is not None:
                _sc = entry.stability_class if entry else _derive_stability_class(node_key)
                _base_temp = entry.temperature if entry else 0.1
                return ResolvedPrompt(
                    node_key=node_key,
                    system_message=(
                        snap_sys_text
                        if snap_sys_text is not None
                        else (entry.system_text if entry else "")
                    ),
                    user_template=(
                        snap_user_text
                        if snap_user_text is not None
                        else (entry.user_text if entry else "")
                    ),
                    temperature=_apply_stability_class_temperature(_base_temp, _sc),
                    max_tokens=entry.max_tokens if entry else 2000,
                    system_version_id=snap_sys_id,
                    user_version_id=snap_user_id,
                    stability_class=_sc,
                )

        entry = self._cache.get(node_key)
        if entry is None:
            if node_key not in self._missing_node_logged:
                self._missing_node_logged.add(node_key)
                logger.warning(
                    f"PromptRegistry: 节点 '{node_key}' 未注册，业务代码应使用代码默认值"
                    " (后续同一 node_key 的未注册调用将降级为 DEBUG, 避免日志风暴)"
                )
            else:
                logger.debug(
                    f"PromptRegistry: 节点 '{node_key}' 仍未注册 (已在首次调用时告警, 本次降级为 DEBUG)"
                )
            # 即使节点未注册，也按 node_key 前缀推导 stability_class，保证
            # 调用方 fallback 到代码默认 prompt 时温度仍受 S.3.2 规则约束
            _sc = _derive_stability_class(node_key)
            return ResolvedPrompt(
                node_key=node_key,
                temperature=_apply_stability_class_temperature(0.1, _sc),
                max_tokens=0,
                stability_class=_sc,
            )

        return ResolvedPrompt(
            node_key=node_key,
            system_message=entry.system_text,
            user_template=entry.user_text,
            temperature=_apply_stability_class_temperature(
                entry.temperature, entry.stability_class,
            ),
            max_tokens=entry.max_tokens,
            system_version_id=entry.system_version_id,
            user_version_id=entry.user_version_id,
            stability_class=entry.stability_class,
        )

    def resolve_snapshot(
        self, node_keys: list[str]
    ) -> dict[str, tuple[Optional[int], Optional[int]]]:
        """批量获取当前激活版本 ID 并预热版本二级缓存（任务启动时调用，锁定版本）。

        返回 dict[node_key -> (system_version_id, user_version_id)]。
        同时把命中节点的 (version_id -> text) 拷贝到 _version_cache,
        确保后续 load/refresh 刷新后, 快照仍能通过 version_id 取到旧版本内容。
        """
        result: dict[str, tuple[Optional[int], Optional[int]]] = {}
        for key in node_keys:
            entry = self._cache.get(key)
            if entry is None:
                continue
            result[key] = (entry.system_version_id, entry.user_version_id)
            if entry.system_version_id and entry.system_text:
                self._version_cache[(key, entry.system_version_id, "system")] = (
                    entry.system_text
                )
            if entry.user_version_id and entry.user_text:
                self._version_cache[(key, entry.user_version_id, "user")] = (
                    entry.user_text
                )
        return result

    def get_node_meta(self, node_key: str) -> Optional[NodeMeta]:
        """获取节点元数据"""
        entry = self._cache.get(node_key)
        return entry.meta if entry else None

    def list_node_keys(self) -> list[str]:
        """获取全部已注册的 node_key"""
        return list(self._cache.keys())

    def record_call(
        self,
        node_key: str,
        project_id: int | None = None,
        latency_ms: float = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: bool = False,
        model_name: str = "",
    ) -> None:
        """异步记录一次 LLM 调用到 execution_traces（fire-and-forget）"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._write_trace(
            node_key, project_id, latency_ms,
            prompt_tokens, completion_tokens, error, model_name,
        ))
        self._pending_traces.append(task)
        task.add_done_callback(self._pending_traces_cleanup)

    def _pending_traces_cleanup(self, task: asyncio.Task) -> None:
        """回调: 任务完成后从 pending 列表移除"""
        try:
            self._pending_traces.remove(task)
        except ValueError:
            pass

    async def flush_pending_traces(self) -> None:
        """等待所有 pending trace 写入完成（billing 结算前调用）"""
        if not self._pending_traces:
            return
        pending = list(self._pending_traces)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _write_trace(
        self,
        node_key: str, project_id: int | None,
        latency_ms: float, prompt_tokens: int,
        completion_tokens: int, error: bool, model_name: str,
    ) -> None:
        try:
            from app.core.database import async_session_factory
            from app.models.llm_execution_trace import LlmExecutionTrace
            from sqlalchemy import select

            async with async_session_factory() as db:
                existing = (await db.execute(
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
                    if model_name:
                        existing.model_name = model_name
                else:
                    db.add(LlmExecutionTrace(
                        node_key=node_key, project_id=project_id,
                        model_name=model_name, trace_mode="aggregate",
                        call_count=1, total_latency_ms=latency_ms,
                        total_prompt_tokens=prompt_tokens,
                        total_completion_tokens=completion_tokens,
                        error_count=1 if error else 0,
                    ))
                await db.commit()
        except Exception as e:
            logger.debug(f"Trace 写入失败(非阻塞): {e}")

    # ──────────────────────────────────────────
    # Registry Sync
    # ──────────────────────────────────────────

    async def registry_sync(self, db: AsyncSession) -> dict:
        """启动时代码 -> DB 同步，调用 Seed 脚本逻辑"""
        from scripts.seed_llm_registry import seed_llm_registry
        stats = await seed_llm_registry(db)
        await self.load(db)
        return stats

    # ──────────────────────────────────────────
    # 跨进程 Pub/Sub (发布 / 订阅)
    # ──────────────────────────────────────────

    async def publish_invalidate(
        self, redis_client: Any, node_key: Optional[str]
    ) -> None:
        """广播 Prompt 缓存失效事件给其它进程.

        Args:
            redis_client: 任一支持 publish() 的 redis 客户端 (ArqRedis 或 redis.asyncio.Redis)
            node_key: None 表示全量 reload, 否则为单节点 refresh

        失败只记录 WARNING, 不影响调用方业务返回。
        """
        if redis_client is None:
            logger.debug(
                f"PromptRegistry.publish_invalidate: redis 不可用, 跳过广播 node_key={node_key}"
            )
            return
        payload: dict[str, Any] = (
            {"type": _INVALIDATE_TYPE_RELOAD}
            if node_key is None
            else {"type": _INVALIDATE_TYPE_REFRESH, "node_key": node_key}
        )
        try:
            await redis_client.publish(INVALIDATE_CHANNEL, json.dumps(payload))
            logger.info(
                f"PromptRegistry: 已广播 invalidate 事件 {payload} -> {INVALIDATE_CHANNEL}"
            )
        except Exception as e:
            logger.warning(
                f"PromptRegistry.publish_invalidate 失败 (不影响业务): node_key={node_key}, err={e}"
            )

    async def start_invalidate_subscriber(self, redis_client: Any) -> None:
        """启动后台订阅协程, 收到其它进程广播的 invalidate 事件即刷新本地缓存.

        幂等: 重复调用不会启动多个订阅协程。
        断连时按指数退避自动重试; 启动失败仅打 WARNING, 不阻塞宿主进程启动。
        """
        if self._invalidate_sub_task is not None and not self._invalidate_sub_task.done():
            logger.debug("PromptRegistry: invalidate subscriber 已在运行, 跳过重复启动")
            return
        sub_client = None
        owns_client = False
        try:
            from app.core.redis import create_redis_pubsub_client

            sub_client = await create_redis_pubsub_client()
            owns_client = sub_client is not None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"PromptRegistry: 独立 Pub/Sub 客户端创建异常, 尝试复用传入 redis: {e}")

        if sub_client is None:
            sub_client = redis_client
        if sub_client is None:
            logger.warning(
                "PromptRegistry: redis 不可用, invalidate subscriber 未启动 "
                "(本进程 Prompt 缓存无法随 API 发布自动刷新, 请重启进程或调用 registry_sync)"
            )
            return

        self._invalidate_sub_stop = asyncio.Event()
        self._invalidate_sub_client = sub_client
        self._invalidate_sub_owns_client = owns_client
        self._invalidate_sub_task = asyncio.create_task(
            self._invalidate_subscriber_loop(sub_client, self._invalidate_sub_stop)
        )
        logger.info(
            f"PromptRegistry: invalidate subscriber 已启动, channel={INVALIDATE_CHANNEL}"
        )

    async def stop_invalidate_subscriber(self) -> None:
        """停止后台订阅协程 (shutdown 调用)"""
        if self._invalidate_sub_stop is not None:
            self._invalidate_sub_stop.set()
        task = self._invalidate_sub_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug(f"PromptRegistry: 订阅协程退出异常 (忽略): {e}")
        self._invalidate_sub_task = None
        self._invalidate_sub_stop = None
        if self._invalidate_sub_owns_client and self._invalidate_sub_client is not None:
            try:
                await self._invalidate_sub_client.aclose()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"PromptRegistry: Pub/Sub 客户端关闭异常 (忽略): {e}")
        self._invalidate_sub_client = None
        self._invalidate_sub_owns_client = False

    async def _invalidate_subscriber_loop(
        self, redis_client: Any, stop_event: asyncio.Event
    ) -> None:
        """订阅循环: 断连时指数退避重连, 直到 stop_event 置位。"""
        backoff = 1.0
        max_backoff = 30.0
        while not stop_event.is_set():
            pubsub = None
            try:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(INVALIDATE_CHANNEL)
                logger.debug(
                    f"PromptRegistry subscriber: 已订阅 {INVALIDATE_CHANNEL}"
                )
                backoff = 1.0  # 订阅成功重置退避
                while not stop_event.is_set():
                    # get_message 等待 1s, 便于检查 stop_event
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is None:
                        continue
                    await self._handle_invalidate_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"PromptRegistry subscriber 异常, {backoff:.1f}s 后重连: {e}"
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    break  # stop_event 已置位
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, max_backoff)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(INVALIDATE_CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
        logger.info("PromptRegistry subscriber 循环退出")

    async def _handle_invalidate_message(self, message: dict) -> None:
        """处理单条 invalidate 消息: 解析 -> 刷新本地缓存"""
        try:
            raw = message.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw) if isinstance(raw, str) else {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PromptRegistry subscriber 消息解析失败 (忽略): {e}")
            return

        msg_type = payload.get("type")
        node_key = payload.get("node_key")
        from app.core.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as db:
                if msg_type == _INVALIDATE_TYPE_RELOAD:
                    await self.load(db)
                    logger.info(
                        "PromptRegistry subscriber: 收到 reload 事件, 已全量刷新缓存"
                    )
                elif msg_type == _INVALIDATE_TYPE_REFRESH and node_key:
                    await self.refresh(db, node_key)
                    logger.info(
                        f"PromptRegistry subscriber: 收到 refresh 事件, 已刷新 node={node_key}"
                    )
                else:
                    logger.debug(
                        f"PromptRegistry subscriber: 未知消息类型, 忽略: {payload}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"PromptRegistry subscriber: 刷新缓存失败 (不影响订阅): {e}"
            )


# ═══════════════════════════════════════════════
# 单例
# ═══════════════════════════════════════════════

_registry: Optional[PromptRegistry] = None


def get_registry() -> PromptRegistry:
    """获取 PromptRegistry 单例"""
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


# ═══════════════════════════════════════════════
# 业务接入 helper
# ═══════════════════════════════════════════════


def resolve_user_template(node_key: str, code_user_template: str) -> str:
    """仅取 user_template 字符串 (Registry 优先, 代码默认兜底)。

    面向业务侧 `.format(...)` 之前的最小侵入替换场景 -- 比如串标共享 system、
    closure 内动态 reduce payload 的调用模式, 调用方保留自己的 format 参数组合。

    示例:
        user_template = resolve_user_template("collusion.pair_compare_entity", COLLUSION_ENTITY_PROMPT)
        user_msg = user_template.format(**payload)
    """
    resolved = get_registry().get_prompt(node_key)
    return resolved.user_template or code_user_template


def resolve_system_prompt(node_key: str, code_system: str) -> str:
    """仅取 system_message 字符串 (Registry 优先, 代码默认兜底)。

    面向 VL/图片识别等 user 不是模板、仅 system 需要热更新的场景。
    """
    resolved = get_registry().get_prompt(node_key)
    return resolved.system_message or code_system


def resolve_runtime_params(
    node_key: str,
    default_temperature: float,
    default_max_tokens: int,
) -> tuple[float, int]:
    """读取节点运行参数，未注册或未配置时回退代码默认值。

    `get_prompt()` 已应用 stability_class，因此 deterministic 节点会在此处
    得到 0.0 温度，避免业务侧只消费 Prompt 文本却继续使用硬编码温度。
    """
    resolved = get_registry().get_prompt(node_key)
    temperature = (
        resolved.temperature
        if resolved.temperature is not None
        else default_temperature
    )
    max_tokens = (
        resolved.max_tokens
        if resolved.max_tokens and resolved.max_tokens > 0
        else default_max_tokens
    )
    return temperature, max_tokens


def registry_first(
    node_key: str,
    code_system: str,
    code_user_template: str,
    /,
    **format_vars: Any,
) -> tuple[str, str]:
    """Registry 优先 + 代码默认值兜底的统一接入 helper。

    典型用法:
        system_msg, user_msg = registry_first(
            "bid_review.tender_extract_page",
            CODE_SYSTEM_PROMPT,
            CODE_USER_TEMPLATE,
            content=chunk_text,
            page_num=page_num,
        )

    行为:
        1. get_prompt(node_key) 命中 Registry -> 用 DB 模板 (自动消费 snapshot/contextvar)
        2. system 与 user 均按 format_vars 做 .format(); 格式化失败回退代码默认
        3. 字面 '{' 需按 Python 约定用 '{{' 转义 (与业务代码现有用法一致)
        4. 任一情况下均返回可直接喂给 LLM 的 (system_message, formatted_user_message)

    注意:
        - format_vars 若为空, 则 system/user 均按字面返回 (常见于 system 为静态文案)
        - 对于 system 与 user 绑定变量不同的节点, 调用方应先自行预处理 system 再传入
    """
    reg = get_registry()
    resolved = reg.get_prompt(node_key)

    system_tpl = resolved.system_message or code_system
    user_tpl = resolved.user_template or code_user_template

    # 格式化 system (失败则回退代码默认)
    if format_vars:
        try:
            system_msg = system_tpl.format(**format_vars)
        except (KeyError, IndexError) as e:
            if resolved.system_message:
                logger.warning(
                    f"registry_first: node={node_key} system_message 格式化失败 {e!r}, 回退代码默认模板"
                )
            try:
                system_msg = code_system.format(**format_vars)
            except (KeyError, IndexError):
                system_msg = code_system
    else:
        system_msg = system_tpl

    # 格式化 user (失败则回退代码默认)
    try:
        user_msg = user_tpl.format(**format_vars) if format_vars else user_tpl
    except (KeyError, IndexError) as e:
        if resolved.user_template:
            logger.warning(
                f"registry_first: node={node_key} user_template 格式化失败 {e!r}, 回退代码默认模板"
            )
        try:
            user_msg = (
                code_user_template.format(**format_vars)
                if format_vars
                else code_user_template
            )
        except (KeyError, IndexError) as e2:
            logger.error(
                f"registry_first: node={node_key} 代码默认模板也格式化失败 {e2!r}, "
                f"回退原始模板字符串"
            )
            user_msg = code_user_template

    return system_msg, user_msg


def call_with_fallback(
    node_key: str,
    code_fn: Callable[..., tuple[str, str]],
    /,
    *args: Any,
    **kwargs: Any,
) -> tuple[str, str]:
    """Registry 优先 + 代码默认兜底的"wrapper 通用器", 自动处理位置→命名参数映射。

 动机 (对齐):
 Registry 里 user_template 通常用命名占位符 (如 "{node_title}"),
 但业务代码历史上散布若干 `_reg_or_code(*a, **kw)` 风格 wrapper,
 调用方可能传位置参数。`str.format(*positional_args, **kwargs)` 不会
 把位置参数绑定到命名占位符, 只会按 "{0}", "{1}" 风格匹配, 所以
 Registry 命中后反而静默 fallback 到代码默认, LLM Center 的在线编辑
 彻底失效。

 实现:
 1. 用 `inspect.signature(code_fn).bind(*args, **kwargs).apply_defaults`
 把混合的 args/kwargs 规范化为纯 kwargs, 对齐 code_fn 的形参名;
 2. Registry 命中 -> `r.user_template.format(**kwargs)`; 格式化失败时
 fallback 到 code_fn(*args, **kwargs) 按原签名调用, 确保业务始终
 有可用 prompt;
 3. code_fn 签名与实际调用不匹配 (TypeError) 时也走代码默认兜底。

 用法替换旧的 `_reg_or_code(node_key, code_fn, *a, **kw)` 本地 helper:

 def get_summarize_prompt(*a, **kw):
 return call_with_fallback("writing.section_summary", _code_summarize, *a, **kw)

 这样调用方无论传位置还是关键字, Registry 的命名占位符都能正确绑定,
 彻底消除 那种"Registry 命中但 format 失败静默 fallback"的陷阱。
 """
    try:
        bound = inspect.signature(code_fn).bind(*args, **kwargs)
        bound.apply_defaults()
        format_kwargs: dict[str, Any] = dict(bound.arguments)
    except TypeError:
        # code_fn 签名与实际调用不匹配 -> 让 code_fn 自己抛出真实错误
        return code_fn(*args, **kwargs)

    resolved = get_registry().get_prompt(node_key)
    if resolved.system_message and resolved.user_template:
        try:
            formatted_system = resolved.system_message
            for key, value in format_kwargs.items():
                placeholder = re.compile(
                    rf"(?<!\{{)\{{{re.escape(str(key))}\}}(?!\}})"
                )
                formatted_system = placeholder.sub(str(value), formatted_system)
            unresolved_system_vars = re.findall(
                r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})",
                formatted_system,
            )
            if unresolved_system_vars:
                raise KeyError(unresolved_system_vars[0])
            formatted_user = resolved.user_template.format(**format_kwargs)
            return formatted_system, formatted_user
        except (KeyError, IndexError) as e:
            logger.warning(
                f"call_with_fallback: node={node_key} registry template 格式化失败 "
                f"{e!r}, 回退代码默认 Prompt"
            )
    return code_fn(*args, **kwargs)


# ═══════════════════════════════════════════════
# 任务级快照: 流程 -> 节点 keys 与便捷绑定器
# ═══════════════════════════════════════════════

# 按流程分组的 node_key 清单, 用于 install_task_snapshot() 一次锁版本。
# 内容与 scripts/seed_llm_registry.py 的运行时 NodeDef 列表一一对应;
# 这里独立维护, 保证 prompt_registry 对 seed 的弱依赖 (避免启动期循环导入)。
FLOW_NODE_KEYS: dict[str, list[str]] = {
    "bid_review": [
        "bid_review.tender_extract_page",
        "bid_review.tender_reflection",
        "bid_review.category_merge_decision",
        "bid_review.secondary_merge_decision",
        "bid_review.bid_extract_match",
        "bid_review.review_item",
        "bid_review.review_no_response",
        "bid_review.image_recognition",
        "bid_review.scoring_extract",
        "bid_review.scoring_repair",
        "bid_review.scoring_full_candidate",
        "bid_review.scheme_reader",
        "bid_review.scoring_table_structure",
        "bid_review.scoring_table_candidate",
        "bid_review.scoring_vlm_rescue",
        "bid_review.scoring_pdf_rescue",
        "bid_review.relevance_classify",
        "judge.review",
    ],
    "collusion": [
        "collusion.tender_format_extract",
        "collusion.route_extract",
        "collusion.pair_compare_entity",
        "collusion.pair_compare_text",
        "collusion.pair_compare_business",
        "collusion.single_dim_search",
        "collusion.pair_judgement",
        "collusion.group_analysis",
        "collusion.project_summary",
        "collusion.overall_synthesis",
        "collusion.industry_signal",
        "collusion.industry_profile_gen",
        "collusion.image_recognition",
        "judge.collusion",
    ],
    "writing": [
        "writing.hard_clause",
        "writing.structure_clause",
        "writing.guide_clause",
        "writing.unified_clause",
        "writing.render_config",
        "writing.clause_dedup_decision",
        "writing.bid_structure_analysis",
        "writing.bid_directory_extract",
        "writing.fixed_template_recognize",
        "writing.clause_classify",
        "writing.relevance_classify",
        "writing.industry_profile",
        "writing.skeleton_generate",
        "writing.section_fill",
        "writing.skeleton_enrich",
        "writing.outline_scope_titles",
        "writing.material_tag",
        "writing.image_classify",
        "writing.chapter_judge",
        "writing.chapter_profile",
        "writing.material_pair_judge",
        "writing.section_plan",
        "writing.data_fill",
        "writing.free_writing",
        "writing.length_adjust",
        "writing.section_summary",
        "writing.cross_ref_check",
        "judge.writer",
    ],
}


def install_task_snapshot(
    flow: str,
) -> Optional[contextvars.Token]:
    """任务入口便捷绑定器: 按流程锁定 Prompt 版本快照到 current_snapshot。

    Args:
        flow: 'bid_review' | 'collusion' | 'writing'

    Returns:
        contextvars.Token 用于 finally 中 reset; 若 flow 未知或快照为空则返回 None。

    典型用法 (worker job):
        token = install_task_snapshot("bid_review")
        try:
            await run_review_task(project_id)
        finally:
            if token is not None:
                current_snapshot.reset(token)

    或使用 async context manager 版本 `bind_task_snapshot(flow)`。
    """
    node_keys = FLOW_NODE_KEYS.get(flow)
    if not node_keys:
        logger.debug(f"install_task_snapshot: 未知 flow={flow}, 跳过快照绑定")
        return None
    try:
        snap = get_registry().resolve_snapshot(node_keys)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"install_task_snapshot: resolve_snapshot 失败 flow={flow}, err={e}, 跳过"
        )
        return None
    if not snap:
        logger.debug(
            f"install_task_snapshot: flow={flow} 当前无任何节点绑定版本 (registry 可能未预热)"
        )
        return None
    token = current_snapshot.set(snap)
    logger.debug(
        f"install_task_snapshot: flow={flow}, 已绑定 {len(snap)} 个节点快照"
    )
    return token


class bind_task_snapshot:
    """async context manager 版本的 install_task_snapshot。

    用法 (worker job):
        async with bind_task_snapshot("bid_review"):
            await run_review_task(project_id)
    """

    def __init__(self, flow: str) -> None:
        self._flow = flow
        self._token: Optional[contextvars.Token] = None

    async def __aenter__(self) -> "bind_task_snapshot":
        self._token = install_task_snapshot(self._flow)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._token is not None:
            try:
                current_snapshot.reset(self._token)
            except ValueError:
                # contextvar 在其它 context 已被 reset, 忽略
                pass
            self._token = None
