"""
职责: 按节点分类将 review 用途调用转发到 extract 或 review 调度器
不做: 不做通道选择/AIMD/参数拼装/观测记录；不修改节点分类数据
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel

NODE_CLASS_MECHANICAL = "mechanical"
NODE_CLASS_JUDGMENT = "judgment"


def resolve_node_key_for_routing(explicit_node_key: Optional[str] = None) -> Optional[str]:
    """解析路由用 node_key：显式参数 > current_node_key 上下文 > None。"""
    if explicit_node_key:
        return explicit_node_key
    try:
        from app.core.ai.prompt_registry import current_node_key
        return current_node_key.get(None)
    except Exception:
        return None


def lookup_node_class(node_key: Optional[str]) -> Optional[str]:
    """从 PromptRegistry 缓存读取 node_class；缺失返回 None（fail-open）。"""
    if not node_key:
        return None
    try:
        from app.core.ai.prompt_registry import get_registry
        meta = get_registry().get_node_meta(node_key)
        if meta is None:
            return None
        return getattr(meta, "node_class", None) or None
    except Exception as exc:
        logger.debug(f"node_pool_router 读取 node_class 失败: {node_key}: {exc}")
        return None


def select_pool_purpose(
    *,
    explicit_node_key: Optional[str] = None,
    extract_has_channels: bool,
) -> str:
    """决定转发目标池。仅当分类确定为 mechanical 且 extract 有通道时返回 extract。"""
    if not extract_has_channels:
        return "review"
    node_key = resolve_node_key_for_routing(explicit_node_key)
    node_class = lookup_node_class(node_key)
    if node_class == NODE_CLASS_MECHANICAL:
        return "extract"
    return "review"


class NodePoolRoutingClient:
    """按节点分类转发到 extract/review 底层客户端的薄代理。"""

    def __init__(
        self,
        review_client: Any,
        extract_client: Any,
        *,
        project_id: Optional[int] = None,
    ) -> None:
        self._review_client = review_client
        self._extract_client = extract_client
        self._project_id = project_id

    @property
    def channel_count(self) -> int:
        review_count = getattr(self._review_client, "channel_count", 0) or 0
        extract_count = getattr(self._extract_client, "channel_count", 0) or 0
        return int(review_count) + int(extract_count)

    def _extract_ready(self) -> bool:
        return bool(getattr(self._extract_client, "channel_count", 0))

    def _target_client(self, *, node_key: Optional[str] = None) -> Any:
        purpose = select_pool_purpose(
            explicit_node_key=node_key,
            extract_has_channels=self._extract_ready(),
        )
        if purpose == "extract":
            return self._extract_client
        return self._review_client

    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        node_key: Optional[str] = None,
    ) -> str:
        client = self._target_client(node_key=node_key)
        kwargs: dict[str, Any] = {
            "user_message": user_message,
            "system_message": system_message,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if node_key is not None:
            kwargs["node_key"] = node_key
        try:
            return await client.chat(**kwargs)
        except TypeError:
            kwargs.pop("node_key", None)
            return await client.chat(**kwargs)

    async def chat_json(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        json_schema: Optional[dict] = None,
        node_key: Optional[str] = None,
        allow_plain_fallback: bool = True,
    ) -> dict:
        client = self._target_client(node_key=node_key)
        kwargs: dict[str, Any] = {
            "user_message": user_message,
            "system_message": system_message,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
            "allow_plain_fallback": allow_plain_fallback,
        }
        if node_key is not None:
            kwargs["node_key"] = node_key
        try:
            return await client.chat_json(**kwargs)
        except TypeError:
            # 底层可能不接受部分关键字；逐步降级
            for drop in ("node_key", "allow_plain_fallback", "json_schema"):
                kwargs.pop(drop, None)
                try:
                    return await client.chat_json(**kwargs)
                except TypeError:
                    continue
            return await client.chat_json(
                user_message, system_message, temperature, max_tokens,
            )

    async def chat_with_images(
        self,
        text_message: str,
        image_data_list: list[str],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        # 视觉调用不按 node_class 分流，固定走 review 侧（vision 另有专用客户端）
        client = self._review_client
        return await client.chat_with_images(
            text_message,
            image_data_list,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_with_validation(
        self,
        user_message: str,
        response_model: type[BaseModel],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        max_retries: int = 3,
        *,
        json_schema: Optional[dict] = None,
        node_key: Optional[str] = None,
        allow_plain_fallback: bool = True,
    ) -> BaseModel:
        client = self._target_client(node_key=node_key)
        kwargs: dict[str, Any] = {
            "user_message": user_message,
            "response_model": response_model,
            "system_message": system_message,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_retries": max_retries,
            "json_schema": json_schema,
            "allow_plain_fallback": allow_plain_fallback,
        }
        if node_key is not None:
            kwargs["node_key"] = node_key
        try:
            return await client.chat_with_validation(**kwargs)
        except TypeError:
            kwargs.pop("node_key", None)
            try:
                return await client.chat_with_validation(**kwargs)
            except TypeError:
                kwargs.pop("allow_plain_fallback", None)
                kwargs.pop("json_schema", None)
                return await client.chat_with_validation(**kwargs)

    async def health_check(self) -> bool:
        return await self._review_client.health_check()

    def get_status(self) -> dict:
        status = {"router": "node_pool", "project_id": self._project_id}
        if hasattr(self._review_client, "get_status"):
            status["review"] = self._review_client.get_status()
        if hasattr(self._extract_client, "get_status"):
            status["extract"] = self._extract_client.get_status()
        return status


def wrap_review_client_with_node_pool_router(
    review_client: Any,
    extract_client: Any,
    *,
    project_id: Optional[int] = None,
) -> NodePoolRoutingClient:
    """将 review 客户端包装为按节点分类路由的代理。"""
    return NodePoolRoutingClient(
        review_client,
        extract_client,
        project_id=project_id,
    )
