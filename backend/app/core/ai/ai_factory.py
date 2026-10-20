"""AI客户端工厂

提供统一的AI客户端接口，根据配置自动选择本地或云端模型。

支持的模式：
- local: 本地Ollama服务（Windows/Mac/Linux兼容）
- cloud: 云端API服务（OpenAI/DeepSeek等）

支持按用途获取客户端:
- review: 审查推理模型
- vision: 视觉识别模型
- extract: 机械提取；CoVe/Judge 复审经 get_judge_client 复用本池（空则 skip，不回落审查）
"""

import asyncio
from typing import Protocol, Optional, Literal, Union
import httpx
from loguru import logger
from pydantic import BaseModel

from app.core.config import get_ai_mode, settings
from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.cloud_client import (
    CloudClient,
    get_cloud_client,
    get_local_client,
    reset_cloud_client,
    reset_local_client,
)


class LLMClientProtocol(Protocol):
    """LLM客户端协议
    
    定义所有LLM客户端必须实现的接口。
    """
    
    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000
    ) -> str:
        """发送聊天请求"""
        ...
    
    async def chat_json(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000
    ) -> dict:
        """发送聊天请求并解析JSON响应"""
        ...
    
    async def chat_with_validation(
        self,
        user_message: str,
        response_model: type[BaseModel],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        max_retries: int = 3
    ) -> BaseModel:
        """发送聊天请求并使用Pydantic校验响应"""
        ...
    
    async def health_check(self) -> bool:
        """健康检查"""
        ...


# 类型别名：统一使用CloudClient（它同时支持本地Ollama和云端API）
LLMClient = Union[CloudClient, AIDispatcher, "ProjectBoundDispatcher", "NodePoolRoutingClient"]


class ProjectBoundDispatcher:
    """轻量代理，将 project_id 自动注入每次 dispatch 调用，保证同一项目始终使用同一模型组"""

    def __init__(self, dispatcher: AIDispatcher, project_id: int):
        self._dispatcher = dispatcher
        self._project_id = project_id

    @property
    def channel_count(self) -> int:
        return self._dispatcher.channel_count

    async def chat(self, user_message: str, system_message: Optional[str] = None,
                   temperature: float = 0.1, max_tokens: int = 2000,
                   *, node_key: Optional[str] = None) -> str:
        """Fix-07: 可选 ``node_key`` 入参；未传时沿用 contextvar，兼容历史调用。"""
        from app.core.ai.determinism import current_project_id as _pid_var
        _pid_var.set(self._project_id)  # S.3.1: seed 派生需要 project_id
        if node_key:
            from app.core.ai.prompt_registry import current_node_key as _nk_var
            _nk_var.set(node_key)
        return await self._dispatcher._dispatch(
            "chat", project_id=self._project_id,
            user_message=user_message, system_message=system_message,
            temperature=temperature, max_tokens=max_tokens)

    async def chat_json(self, user_message: str, system_message: Optional[str] = None,
                        temperature: float = 0.1, max_tokens: int = 2000,
                        *, json_schema: Optional[dict] = None,
                        node_key: Optional[str] = None,
                        allow_plain_fallback: bool = True) -> dict:
        """Fix-07: 可选 ``node_key`` 入参；未传时沿用 contextvar，兼容历史调用。"""
        from app.core.ai.determinism import current_project_id as _pid_var
        _pid_var.set(self._project_id)
        if node_key:
            from app.core.ai.prompt_registry import current_node_key as _nk_var
            _nk_var.set(node_key)
        return await self._dispatcher._dispatch(
            "chat_json", project_id=self._project_id,
            user_message=user_message, system_message=system_message,
            temperature=temperature, max_tokens=max_tokens,
            json_schema=json_schema,
            allow_plain_fallback=allow_plain_fallback)

    async def chat_with_images(self, text_message: str, image_data_list: list[str],
                               system_message: Optional[str] = None,
                               temperature: float = 0.1, max_tokens: int = 2000) -> str:
        from app.core.ai.determinism import current_project_id as _pid_var
        _pid_var.set(self._project_id)
        return await self._dispatcher._dispatch(
            "chat_with_images", project_id=self._project_id,
            text_message=text_message, image_data_list=image_data_list,
            system_message=system_message, temperature=temperature, max_tokens=max_tokens)

    async def chat_with_validation(self, user_message: str, response_model,
                                   system_message: Optional[str] = None,
                                   temperature: float = 0.1, max_tokens: int = 2000,
                                   max_retries: int = 3,
                                   *, json_schema: Optional[dict] = None,
                                   allow_plain_fallback: bool = True):
        from app.core.ai.determinism import current_project_id as _pid_var
        _pid_var.set(self._project_id)
        return await self._dispatcher._dispatch(
            "chat_with_validation", project_id=self._project_id,
            user_message=user_message, response_model=response_model,
            system_message=system_message, temperature=temperature,
            max_tokens=max_tokens, max_retries=max_retries,
            json_schema=json_schema,
            allow_plain_fallback=allow_plain_fallback)

    async def health_check(self) -> bool:
        return await self._dispatcher.health_check()

    def get_status(self) -> dict:
        return self._dispatcher.get_status()


# 按配置ID缓存的客户端实例
_config_clients: dict[str, CloudClient] = {}

# 全局 Dispatcher 实例（按用途）
_review_dispatcher: Optional[AIDispatcher] = None
_vision_dispatcher: Optional[AIDispatcher] = None
_judge_dispatcher: Optional[AIDispatcher] = None  # 复审已并入 extract；仅供 reset 清空历史缓存
_extract_dispatcher: Optional[AIDispatcher] = None


def get_llm_client(mode: Optional[str] = None) -> LLMClient:
    """获取LLM客户端
    
    根据配置或指定模式返回对应的客户端实例。
    
    Args:
        mode: AI模式 (local/cloud)，不指定则使用配置
        
    Returns:
        LLMClient: 本地或云端LLM客户端
        
    Raises:
        ValueError: 无效的AI模式
    """
    current_mode = mode or get_ai_mode()
    
    if current_mode == "local":
        logger.debug("使用本地LLM客户端 (Ollama)")
        return get_local_client()
    elif current_mode == "cloud":
        logger.debug("使用云端LLM客户端")
        return get_cloud_client()
    else:
        raise ValueError(f"无效的AI模式: {current_mode}")


def get_client_by_config_id(config_id: str, purpose: str = "review") -> LLMClient:
    """根据配置ID获取LLM客户端
    
    从ai_settings中查找指定用途的配置并创建客户端。
    支持本地和云端配置（统一处理）。
    
    Args:
        config_id: 配置ID（空字符串则回退到默认本地客户端）
        purpose: 用途 ("vision" 或 "review")
        
    Returns:
        LLMClient: 对应的客户端实例
    """
    # 空ID或旧的"local"值: 回退到默认本地客户端
    if not config_id or config_id == "local":
        return get_local_client()
    
    # 缓存 key 包含 purpose, 避免不同用途的同名 config_id 碰撞
    global _config_clients
    cache_key = f"{purpose}_{config_id}"
    if cache_key in _config_clients:
        return _config_clients[cache_key]
    
    # 从设置中查找配置
    from app.core.ai_settings import load_ai_settings
    ai_settings = load_ai_settings()
    config = ai_settings.get_config_by_id(purpose, config_id)  # type: ignore
    
    if not config:
        logger.warning(f"配置不存在: {config_id} (purpose={purpose}), 回退到默认本地客户端")
        return get_local_client()
    
    api_key = config.api_key or ("ollama" if config.config_type == "local" else "")
    
    if config.config_type == "cloud" and not api_key:
        raise ValueError(f"配置 {config_id} 未设置API密钥")
    
    timeout = 300.0 if config.config_type == "local" else settings.CLOUD_TIMEOUT
    client = CloudClient(
        base_url=config.api_base,
        api_key=api_key,
        model_name=config.model_name,
        timeout=timeout,
        default_temperature=config.temperature,
        temperature_mode=config.temperature_mode,
        temperature_exact=config.temperature_exact,
        temperature_min=config.temperature_min,
        temperature_max=config.temperature_max,
    )
    _config_clients[cache_key] = client
    
    logger.info(f"已创建配置客户端: purpose={purpose}, id={config_id}, model={config.model_name}")
    return client


def _build_dispatcher(purpose: str) -> AIDispatcher:
    """从 ai_settings 构建指定用途的调度器（遍历调度池中所有配置）

    同一 (base_url, timeout) 下的多个通道共享同一个 httpx.AsyncClient,
    避免每个 CloudClient 各自创建独立的 HTTP 客户端。
    """
    from app.core.ai_settings import load_ai_settings
    ai_settings = load_ai_settings()
    pool_ids = ai_settings.get_pool_ids(purpose)
    dispatcher = AIDispatcher(purpose=purpose)

    config_ids = pool_ids if pool_ids else []
    if not config_ids:
        fallback_id = ai_settings.get_model_id(purpose)
        if fallback_id:
            config_ids = [fallback_id]

    httpx_pool: dict[tuple[str, float], httpx.AsyncClient] = {}

    for cid in config_ids:
        config = ai_settings.get_config_by_id(purpose, cid)
        if not config:
            logger.warning(f"Dispatcher[{purpose}] 池中配置不存在: {cid}, 跳过")
            continue

        priority = ai_settings.get_priority_for(purpose, cid)
        timeout = 300.0 if config.config_type == "local" else settings.CLOUD_TIMEOUT
        pool_key = (config.api_base, timeout)
        if pool_key not in httpx_pool:
            httpx_pool[pool_key] = httpx.AsyncClient(
                timeout=timeout,
            )

        shared_http = httpx_pool[pool_key]
        keys = config.get_all_keys()
        cfg_temp = config.temperature
        if not keys:
            api_key = config.api_key or ("ollama" if config.config_type == "local" else "")
            if not api_key:
                continue
            client = CloudClient(
                base_url=config.api_base, api_key=api_key,
                model_name=config.model_name, timeout=timeout,
                retry_on_429=False, http_client=shared_http,
                default_temperature=cfg_temp,
                temperature_mode=config.temperature_mode,
                temperature_exact=config.temperature_exact,
                temperature_min=config.temperature_min,
                temperature_max=config.temperature_max,
                capability_profile=config.capabilities,
            )
            dispatcher.register_channel(client, config.id, key_index=0, model_group=cid, priority=priority)
        else:
            for idx, key in enumerate(keys):
                client = CloudClient(
                    base_url=config.api_base, api_key=key,
                    model_name=config.model_name, timeout=timeout,
                    retry_on_429=False, http_client=shared_http,
                    default_temperature=cfg_temp,
                    temperature_mode=config.temperature_mode,
                    temperature_exact=config.temperature_exact,
                    temperature_min=config.temperature_min,
                    temperature_max=config.temperature_max,
                    capability_profile=config.capabilities,
                )
                dispatcher.register_channel(client, config.id, key_index=idx, model_group=cid, priority=priority)

    if dispatcher.channel_count > 0:
        groups = dispatcher._get_model_groups()
        httpx_count = len(httpx_pool)
        group_models = ", ".join(
            f"{gid}({chs[0].client.model_name}x{len(chs)})"
            for gid, chs in groups.items()
        )
        logger.info(
            f"Dispatcher[{purpose}] 已构建: "
            f"{dispatcher.channel_count} 个通道, {len(groups)} 个模型组, "
            f"{httpx_count} 个共享HTTP客户端"
        )
        logger.info(f"Dispatcher[{purpose}] 模型组明细: {group_models}")
    return dispatcher


def _raw_review_client(project_id: Optional[int] = None) -> LLMClient:
    """获取未包装的审查客户端（供 extract 空池回落与路由包装内层使用）。"""
    global _review_dispatcher
    if _review_dispatcher is None:
        _review_dispatcher = _build_dispatcher("review")
    if _review_dispatcher.channel_count > 0:
        if project_id is not None:
            return ProjectBoundDispatcher(_review_dispatcher, project_id)
        return _review_dispatcher
    from app.core.ai_settings import load_ai_settings
    ai_settings = load_ai_settings()
    return get_client_by_config_id(ai_settings.review_model_id, purpose="review")


def get_review_client(project_id: Optional[int] = None) -> LLMClient:
    """获取审查模型客户端（优先返回 Dispatcher，支持项目级绑定）

    extract 池有通道时包装 NodePoolRoutingClient：机械类节点转发 extract，
    其余 fail-open 走 review。extract 池空时行为与历史完全一致。
    """
    review_client = _raw_review_client(project_id)
    global _extract_dispatcher
    if _extract_dispatcher is None:
        _extract_dispatcher = _build_dispatcher("extract")
    if _extract_dispatcher.channel_count <= 0:
        return review_client
    extract_client: LLMClient
    if project_id is not None:
        extract_client = ProjectBoundDispatcher(_extract_dispatcher, project_id)
    else:
        extract_client = _extract_dispatcher
    from app.core.ai.node_pool_router import wrap_review_client_with_node_pool_router
    return wrap_review_client_with_node_pool_router(
        review_client, extract_client, project_id=project_id,
    )


def get_vision_client(project_id: Optional[int] = None) -> LLMClient:
    """获取视觉模型客户端（优先返回 Dispatcher，支持项目级绑定）"""
    global _vision_dispatcher
    if _vision_dispatcher is None:
        _vision_dispatcher = _build_dispatcher("vision")
    if _vision_dispatcher.channel_count > 0:
        if project_id is not None:
            return ProjectBoundDispatcher(_vision_dispatcher, project_id)
        return _vision_dispatcher
    from app.core.ai_settings import load_ai_settings
    ai_settings = load_ai_settings()
    return get_client_by_config_id(ai_settings.vision_model_id, purpose="vision")


def get_review_dispatcher() -> Optional[AIDispatcher]:
    """获取审查调度器实例（供状态查询使用）"""
    return _review_dispatcher


def get_vision_dispatcher() -> Optional[AIDispatcher]:
    """获取视觉调度器实例（供状态查询使用）"""
    return _vision_dispatcher


def get_judge_client(project_id: Optional[int] = None) -> Optional[LLMClient]:
    """为 CoVe/Judge 提供客户端，复用提取池。

    职责: 提取池有通道时返回提取调度器（可按项目绑定）。
    不做: 不读取 judge 池配置；提取池空时不回落审查，返回 None 供调用方 skip。
    """
    global _extract_dispatcher
    if _extract_dispatcher is None:
        _extract_dispatcher = _build_dispatcher("extract")
    if _extract_dispatcher.channel_count > 0:
        if project_id is not None:
            return ProjectBoundDispatcher(_extract_dispatcher, project_id)
        return _extract_dispatcher
    return None


def get_judge_dispatcher() -> Optional[AIDispatcher]:
    """复审已并入提取池，状态查询返回 extract 调度器。"""
    return get_extract_dispatcher()


def get_extract_client(project_id: Optional[int] = None) -> LLMClient:
    """获取提取模型客户端（池空时 fail-open 回落 raw review）。

    CoVe/Judge 复审经 get_judge_client 复用本调度器，但空池语义不同：
    提取回落审查，复审返回 None 并 skip。

    注意：空池回落必须走 ``_raw_review_client``，禁止调用 ``get_review_client``，
    否则与 NodePoolRoutingClient 包装形成递归。
    """
    global _extract_dispatcher
    if _extract_dispatcher is None:
        _extract_dispatcher = _build_dispatcher("extract")
    if _extract_dispatcher.channel_count > 0:
        if project_id is not None:
            return ProjectBoundDispatcher(_extract_dispatcher, project_id)
        return _extract_dispatcher
    return _raw_review_client(project_id)


def get_extract_dispatcher() -> Optional[AIDispatcher]:
    """获取提取调度器实例（供状态查询使用）"""
    return _extract_dispatcher


def sync_channel_priorities(purpose: Optional[str] = None) -> None:
    """仅同步优先级到运行中的调度器，不重建通道

    优先级变更时调用此函数代替 reset_config_clients，
    避免丢失熔断/AIMD/延迟统计等运行时状态。
    """
    from app.core.ai_settings import load_ai_settings
    ai_settings = load_ai_settings()
    for p, dispatcher in [
        ("review", _review_dispatcher),
        ("vision", _vision_dispatcher),
        ("extract", _extract_dispatcher),
    ]:
        if purpose and p != purpose:
            continue
        if dispatcher and dispatcher.channel_count > 0:
            priority_map = {
                cid: ai_settings.get_priority_for(p, cid)
                for cid in ai_settings.get_pool_ids(p)
            }
            dispatcher.update_channel_priorities(priority_map)
    logger.info(f"AI通道优先级已热更新({purpose or 'all'})")


def reset_config_clients(purpose: Optional[str] = None) -> None:
    """重置按配置ID缓存的客户端和调度器,并立即重建

    配置变更(add/update/delete/set_pool)时由 _sync_to_runtime 调用,
    保证 Dispatcher 始终与最新持久化配置同步。

    Args:
        purpose: 指定仅重建 "review" 或 "vision" 侧,None 则全量重建
    """
    global _config_clients, _review_dispatcher, _vision_dispatcher, _judge_dispatcher, _extract_dispatcher

    if purpose is None or purpose == "review":
        _config_clients = {
            k: v for k, v in _config_clients.items()
            if not k.startswith("review_")
        }
        _review_dispatcher = _build_dispatcher("review")

    if purpose is None or purpose == "vision":
        _config_clients = {
            k: v for k, v in _config_clients.items()
            if not k.startswith("vision_")
        }
        _vision_dispatcher = _build_dispatcher("vision")

    if purpose is None or purpose == "judge":
        _config_clients = {
            k: v for k, v in _config_clients.items()
            if not k.startswith("judge_")
        }
        _judge_dispatcher = None

    if purpose is None or purpose == "extract":
        _config_clients = {
            k: v for k, v in _config_clients.items()
            if not k.startswith("extract_")
        }
        _extract_dispatcher = _build_dispatcher("extract")

    r_ch = _review_dispatcher.channel_count if _review_dispatcher else 0
    v_ch = _vision_dispatcher.channel_count if _vision_dispatcher else 0
    e_ch = _extract_dispatcher.channel_count if _extract_dispatcher else 0
    label = purpose or "all"
    logger.info(
        f"AI通道已重建({label}): review={r_ch}通道, vision={v_ch}通道, "
        f"extract={e_ch}通道 (复审并入提取)"
    )


def init_dispatchers() -> None:
    """应用启动阶段预构建 review 和 vision 调度器

    将延迟初始化提前到 lifespan 阶段,消除首次业务请求的构建开销。
    仅构建通道对象,不执行网络请求(连通性由 probe_dispatchers 负责)。
    若 reset_config_clients 已在 init_ai_settings 中完成构建则跳过重复创建。
    """
    global _review_dispatcher, _vision_dispatcher, _extract_dispatcher
    if _review_dispatcher is None:
        _review_dispatcher = _build_dispatcher("review")
    if _vision_dispatcher is None:
        _vision_dispatcher = _build_dispatcher("vision")
    if _extract_dispatcher is None:
        _extract_dispatcher = _build_dispatcher("extract")
    r_ch = _review_dispatcher.channel_count
    v_ch = _vision_dispatcher.channel_count
    e_ch = _extract_dispatcher.channel_count
    logger.info(
        f"AI通道预热完成: review={r_ch}通道, vision={v_ch}通道, "
        f"extract={e_ch}通道 (复审并入提取)"
    )
    if r_ch == 0:
        logger.warning("审查调度器无可用通道,请在设置页面配置模型")
    if v_ch == 0:
        logger.warning("视觉调度器无可用通道,请在设置页面配置模型")
    if e_ch == 0:
        logger.warning("提取调度器无可用通道,机械提取将回落审查; CoVe/Judge 复审将跳过")


async def probe_dispatchers() -> None:
    """启动阶段基准测试: 探测延迟 + 微调通道优先级(非阻塞,失败不影响启动)"""
    await benchmark_dispatchers()


async def benchmark_dispatchers() -> None:
    """对各通道做轻量基准测试，据此微调 priority

    - 每通道发送 "hi"(max_tokens=5) 测延迟，单通道超时 10s
    - 通道间错峰 200ms 避免瞬时并发触发限流
    - 探测失败: priority += 50 (惩罚但不跨越档位)
    - 同档内按延迟排名: priority += rank (0~N)
    - 总超时 20s 保护启动流程
    """
    import time as _time
    from app.core.ai.attempt_recorder import current_traffic_class

    # P0-A: 启动探测不计入生产可靠性统计(startup task 结束即失效)
    current_traffic_class.set("admin_test")

    for purpose, dispatcher in [
        ("review", _review_dispatcher),
        ("vision", _vision_dispatcher),
        ("extract", _extract_dispatcher),
    ]:
        if not dispatcher or dispatcher.channel_count == 0:
            continue

        channels = dispatcher._channels

        async def _probe_one(ch, index: int):
            # P0-B: 真实健康探测——视觉池发真实图片、空正文/截断判失败,
            # 替代旧 "hi"+max_tokens=5 的假绿探测。
            from app.core.ai.health_probe import run_primary_probe
            await asyncio.sleep(index * 0.2)
            try:
                report = await run_primary_probe(ch.client, purpose=purpose)
                ch.probe_latency_ms = (
                    float(report.latency_ms) if report.ok else 99999
                )
                ch.probe_success = report.ok
                ch.last_probe = report.to_dict()
            except Exception as e:
                ch.probe_latency_ms = 99999
                ch.probe_success = False
                ch.last_probe = {"kind": "primary", "ok": False, "error": str(e)[:200]}

        _total_timeout = 60 if purpose == "vision" else 30
        try:
            await asyncio.wait_for(
                asyncio.gather(*[_probe_one(ch, i) for i, ch in enumerate(channels)]),
                timeout=_total_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Dispatcher[{purpose}] 基准测试总超时({_total_timeout}s)")

        base_priorities: dict[int, list] = {}
        for ch in channels:
            base_priorities.setdefault(ch.priority, []).append(ch)

        for base_p, group_channels in base_priorities.items():
            sorted_chs = sorted(group_channels, key=lambda c: c.probe_latency_ms)
            for rank, ch in enumerate(sorted_chs):
                adjustment = 0
                if not ch.probe_success:
                    adjustment += 50
                adjustment += rank
                ch.priority = base_p + adjustment

        ok_count = sum(1 for ch in channels if ch.probe_success)
        fail_count = len(channels) - ok_count
        logger.info(
            f"Dispatcher[{purpose}] 基准测试完成: "
            f"{ok_count} 通过, {fail_count} 失败, "
            f"通道优先级已微调"
        )
        if fail_count > 0:
            for ch in channels:
                if not ch.probe_success:
                    logger.warning(
                        f"  通道探测失败: {ch.config_id}#{ch.key_index} "
                        f"model={ch.client.model_name}"
                    )


def get_available_modes_sync() -> dict:
    """获取可用的AI模式信息（同步版本，不执行健康检查）
    
    用于快速获取配置信息，不检查服务可用性。
    
    Returns:
        dict: 模式信息
    """
    return {
        "current_mode": get_ai_mode(),
        "modes": {
            "local": {
                "name": "本地模型 (Ollama)",
                "description": f"使用本地Ollama服务: {settings.LLM_MODEL_NAME}",
                "available": True,  # 假设可用，实际通过测试连接确认
                "base_url": settings.LLM_API_BASE,
                "model": settings.LLM_MODEL_NAME
            },
            "cloud": {
                "name": "云端模型",
                "description": f"使用云端API: {settings.CLOUD_MODEL_NAME}",
                "available": bool(settings.CLOUD_API_KEY),  # 有密钥就认为可用
                "base_url": settings.CLOUD_API_BASE,
                "model": settings.CLOUD_MODEL_NAME,
                "api_key_configured": bool(settings.CLOUD_API_KEY)
            }
        }
    }


async def get_available_modes() -> dict:
    """获取可用的AI模式信息（异步版本，不执行健康检查）
    
    为了提高响应速度，不再在此执行健康检查。
    用户可通过"测试连接"按钮手动检查服务可用性。
    
    Returns:
        dict: 模式信息
    """
    # 直接返回同步版本的结果，避免阻塞
    return get_available_modes_sync()


# B2(D-009): AIClientManager 已删除，cloud→local fallback 语义由 AIDispatcher 跨组降级覆盖。
# 保留 get_ai_manager 作为 deprecated 别名，下一批次（B3）移除。
AIClientManager = None


def get_ai_manager(fallback: bool = True) -> LLMClient:
    """deprecated: 使用 get_review_client() 或 get_llm_client() 代替。"""
    import warnings
    warnings.warn(
        "get_ai_manager() 已弃用(B2 D-009)，请使用 get_review_client() 代替",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_llm_client()
