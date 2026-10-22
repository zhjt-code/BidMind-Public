"""统一LLM客户端

封装OpenAI兼容API调用，支持本地和云端模型。
- 本地模式：Ollama (Windows/Mac/Linux)
- 云端模式：OpenAI、DeepSeek、智谱AI、通义千问等
"""

import asyncio
import json
import re
import threading
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

from loguru import logger
from openai import APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.ai.attempt_recorder import get_attempt_recorder
from app.core.ai.json_text_cleaning import strip_think_and_code_fences
from app.core.ai.llm_errors import EmptyLLMResponseError
from app.core.ai.llm_outcome import LlmOutcome
from app.core.ai.metrics import record_llm_call
from app.core.ai.vision_image_payload import build_vision_image_payloads
from app.core.config import settings
from app.core.observability.metrics import empty_return_total, json_repair_total

T = TypeVar("T")

__all__ = [
    "CloudClient",
    "EmptyLLMResponseError",
    "LlmContentValidationError",
    "UnsupportedResponseFormatError",
]


class UnsupportedResponseFormatError(Exception):
    """标识 provider/model 不支持 OpenAI 兼容 response_format 的显式信号。

    dispatcher 捕获后不计通道故障（不熔断），并优先跨组而非继续磨同组 key；
    调用方（chat_json 等）在本实例内捕获后回退为普通 chat + JSON 解析，
    保持对外接口契约不变。
    """


class LlmContentValidationError(ValueError):
    """标识 LLM 输出重试耗尽后仍无法通过 JSON/Pydantic 内容校验的显式信号。

 继承 ValueError，对 chat_with_validation 既有调用方完全向后兼容。
 dispatcher 捕获后不计通道故障（不熔断），并把当前模型组加入 avoid_groups
 强制跨组换模型——同模型在确定性/低温配置下对同一 prompt 的形状偏差
 必然复现，换同组 key 盲重试没有意义。

 suggested_max_tokens: 截断自动扩容后的建议 max_tokens。
 非 None 表示本通道内已确认初始预算不足——dispatcher 跨组换模型时应续用该值，
 否则下一模型从初始预算重新开始，截断类故障在所有模型上必然复现。
 """

    def __init__(
        self, message: str, *, suggested_max_tokens: Optional[int] = None
    ) -> None:
        super().__init__(message)
        self.suggested_max_tokens = suggested_max_tokens


# 进阶修复（复核降级链路加固）：把 response_format 能力探测
# 从单个 CloudClient 实例级提升到 (base_url, model_name) 进程级，
# 避免 dispatcher 多 key 通道场景下每个 key 都要被刺一次 400。
_UNSUPPORTED_RESPONSE_FORMAT_CACHE: set[tuple[str, str]] = set()
_UNSUPPORTED_RESPONSE_FORMAT_LOCK = threading.Lock()


def _is_response_format_unsupported(base_url: str, model_name: str) -> bool:
    with _UNSUPPORTED_RESPONSE_FORMAT_LOCK:
        return (base_url, model_name) in _UNSUPPORTED_RESPONSE_FORMAT_CACHE


def _mark_response_format_unsupported(base_url: str, model_name: str) -> None:
    with _UNSUPPORTED_RESPONSE_FORMAT_LOCK:
        _UNSUPPORTED_RESPONSE_FORMAT_CACHE.add((base_url, model_name))


def _clear_response_format_cache() -> None:
    """仅供测试复位使用。"""
    with _UNSUPPORTED_RESPONSE_FORMAT_LOCK:
        _UNSUPPORTED_RESPONSE_FORMAT_CACHE.clear()


# 部分新一代云端模型（OpenAI o1/o3/GPT-5、aihubmix gemini-3-flash-preview 等）
# 已废弃 max_tokens，强制要求 max_completion_tokens。复刻 response_format 兼容机制：
# 进程级缓存 (base_url, model_name) -> 已知必须用 max_completion_tokens；
# 首次 400 错误后写缓存并自动重试一次，后续同模型直接走新参数名。
_MAX_COMPLETION_TOKENS_REQUIRED_CACHE: set[tuple[str, str]] = set()
_MAX_COMPLETION_TOKENS_REQUIRED_LOCK = threading.Lock()


def _is_max_completion_tokens_required(base_url: str, model_name: str) -> bool:
    with _MAX_COMPLETION_TOKENS_REQUIRED_LOCK:
        return (base_url, model_name) in _MAX_COMPLETION_TOKENS_REQUIRED_CACHE


def _mark_max_completion_tokens_required(base_url: str, model_name: str) -> None:
    with _MAX_COMPLETION_TOKENS_REQUIRED_LOCK:
        _MAX_COMPLETION_TOKENS_REQUIRED_CACHE.add((base_url, model_name))


def _clear_max_completion_tokens_cache() -> None:
    """仅供测试复位使用。"""
    with _MAX_COMPLETION_TOKENS_REQUIRED_LOCK:
        _MAX_COMPLETION_TOKENS_REQUIRED_CACHE.clear()


def _apply_token_limit_kwargs(base_url: str, model_name: str, max_tokens: int) -> dict:
    """根据进程级兼容性缓存返回正确的 token 限制参数。

    Returns:
        {"max_completion_tokens": N} 若该模型已知不支持 max_tokens；
        否则返回 {"max_tokens": N}。
    """
    if _is_max_completion_tokens_required(base_url, model_name):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _is_max_tokens_unsupported_error(exc: Exception) -> bool:
    """识别 OpenAI 兼容服务返回的 'max_tokens 不支持' 信号。

    匹配特征基于 OpenAI 官方错误格式：
        "Unsupported parameter: 'max_tokens' is not supported with this model.
         Use 'max_completion_tokens' instead."
    同时容忍代理服务（如 aihubmix）转译的相近文案。
    """
    msg = str(exc)
    return (
        "max_tokens" in msg
        and "max_completion_tokens" in msg
        and ("not supported" in msg or "unsupported" in msg.lower())
    )


class CloudClient:
    """云端LLM客户端
    
    使用 OpenAI 兼容 API 调用云端 LLM 服务。
    
    示例:
        >>> client = CloudClient()
        >>> response = await client.chat("你好")
        >>> print(response)
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: float = 120.0,
        retry_on_429: bool = True,
        http_client: Optional[Any] = None,
        default_temperature: Optional[float] = None,
        temperature_mode: str = "free",
        temperature_exact: Optional[float] = None,
        temperature_min: Optional[float] = None,
        temperature_max: Optional[float] = None,
        capability_profile: Optional[Any] = None,
    ):
        """初始化云端客户端
        
        Args:
            base_url: API基础URL，默认使用配置
            api_key: API密钥，默认使用配置
            model_name: 模型名称，默认使用配置
            timeout: 请求超时时间（秒）
            http_client: 共享的 httpx.AsyncClient 实例，传入后 AsyncOpenAI
                         复用该客户端而非内部新建，可大幅减少初始化开销
            default_temperature: 模型级别的默认采样温度，None 则使用全局默认 0.1
            temperature_mode: 温度约束模式（free/exact/clamp）
            temperature_exact: 固定温度值（temperature_mode=exact 时生效）
            temperature_min: 最小温度（temperature_mode=clamp 时生效）
            temperature_max: 最大温度（temperature_mode=clamp 时生效）
        """
        self.base_url = base_url or settings.CLOUD_API_BASE
        self.api_key = api_key or settings.CLOUD_API_KEY
        self.model_name = model_name or settings.CLOUD_MODEL_NAME
        self.timeout = timeout or settings.CLOUD_TIMEOUT
        self._default_temperature = default_temperature
        self._temperature_mode = temperature_mode
        self._temperature_exact = temperature_exact
        self._temperature_min = temperature_min
        self._temperature_max = temperature_max
        # P0-C: 能力档案(thinking/seed/结构化能力整形与路由消费)
        self.capability_profile = capability_profile
        
        if not self.api_key:
            logger.warning(
                "云端API密钥未设置，请在.env文件中配置 CLOUD_API_KEY"
            )
        
        # P0-B: 关闭 SDK 隐式重试(默认 2 次)。重试决策统一归 Dispatcher,
        # 否则一次逻辑调用的物理请求次数不可预算、不可追溯。
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "sk-placeholder",
            timeout=self.timeout,
            max_retries=0,
            http_client=http_client,
        )
        self._retry_on_429 = retry_on_429
        self._last_usage: Optional[dict] = None
        self._last_finish_reason: Optional[str] = None
        self._json_schema_unsupported: bool = False
        
        logger.info(
            f"CloudClient 初始化: base_url={self.base_url}, "
            f"model={self.model_name}"
        )

    _GLOBAL_DEFAULT_TEMPERATURE = 0.1

    def _resolve_temperature(self, temperature: float) -> float:
        """确定实际使用的温度值。

        决议见 temperature_params.resolve_request_temperature:
        exact/clamp(用户能力约束) > 显式业务温度 > 模型缺省(仅签名默认 0.1)。
        """
        original = (
            self._default_temperature
            if (
                temperature == self._GLOBAL_DEFAULT_TEMPERATURE
                and self._default_temperature is not None
            )
            else temperature
        )
        from app.core.ai.temperature_params import (
            TemperatureSettings,
            resolve_request_temperature,
        )

        resolved = resolve_request_temperature(
            temperature,
            TemperatureSettings(
                default_temperature=self._default_temperature,
                mode=self._temperature_mode,
                exact=self._temperature_exact,
                min_value=self._temperature_min,
                max_value=self._temperature_max,
            ),
        )

        if resolved != original:
            logger.info(
                f"温度已按模型能力归一化: model={self.model_name}, "
                f"requested={original}, resolved={resolved}, mode={self._temperature_mode}"
            )
        return resolved

    def _resolve_seed(self, explicit_seed: Optional[int]) -> Optional[int]:
        """S.3.1: 按调用链 contextvar + project_id 派生稳定 seed。

        优先级: 调用方显式传入 > determinism.resolve_current_seed（读
        prompt_registry.current_node_key + CloudClient 实例级 project_id）。
        当 settings.LLM_SEED_ENABLED = False 时短路返回 None。
        """
        try:
            from app.core.ai.determinism import resolve_current_seed
            pid = getattr(self, "_project_id", None)
            return resolve_current_seed(project_id=pid, explicit_seed=explicit_seed)
        except Exception:
            return explicit_seed

    async def _call_with_retry(
        self,
        call_fn: Callable[[], Awaitable[T]],
        max_retries: int = 3,
        label: str = "LLM",
    ) -> T:
        """带 429 限流退避的统一调用包装"""
        _method = label.split("[", 1)[0]
        for attempt in range(max_retries):
            start = time.monotonic()
            try:
                result = await call_fn()
                elapsed_ms = (time.monotonic() - start) * 1000
                get_attempt_recorder().record_success(
                    result, latency_ms=int(elapsed_ms),
                    model_name=self.model_name, method=_method,
                )
                logger.debug(f"{label} 调用完成: {elapsed_ms:.0f}ms, model={self.model_name}")
                return result
            except RateLimitError as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                get_attempt_recorder().record_error(
                    e, latency_ms=int(elapsed_ms),
                    model_name=self.model_name, method=_method,
                )
                if not self._retry_on_429 or attempt == max_retries - 1:
                    reason = (
                        "未启用本层重试，交由上层调度"
                        if not self._retry_on_429
                        else "重试耗尽"
                    )
                    logger.error(f"{label} 限流(429){reason} ({elapsed_ms:.0f}ms): {e}")
                    raise
                wait = min(2 ** attempt * 5, 60)
                logger.warning(
                    f"{label} 触发限流(429), {wait}s 后重试 "
                    f"(attempt {attempt + 1}/{max_retries}, {elapsed_ms:.0f}ms): {e}"
                )
                await asyncio.sleep(wait)
            except APIStatusError as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                get_attempt_recorder().record_error(
                    e, latency_ms=int(elapsed_ms),
                    model_name=self.model_name, method=_method,
                )
                if e.status_code == 429 and self._retry_on_429 and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    logger.warning(
                        f"{label} API 429, {wait}s 后重试 "
                        f"(attempt {attempt + 1}/{max_retries}, {elapsed_ms:.0f}ms)"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"{label} API 错误 (status={e.status_code}, {elapsed_ms:.0f}ms): {e}")
                    raise
            except Exception as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                get_attempt_recorder().record_error(
                    e, latency_ms=int(elapsed_ms),
                    model_name=self.model_name, method=_method,
                )
                logger.error(f"{label} 调用失败 ({elapsed_ms:.0f}ms): {e}")
                raise
        raise RuntimeError(f"{label} 重试逻辑异常")

    async def _call_completions_create(
        self,
        do_call: Callable[[], Awaitable[T]],
        label: str,
    ) -> T:
        """带 max_tokens / max_completion_tokens 自动降级的统一调用包装。

        do_call 必须是闭包，且每次执行都通过 _apply_token_limit_kwargs 动态读取
        进程级缓存。第一次 400 'max_tokens not supported' 后写入缓存，立即重试一次
        让 do_call 拿到新参数名；同 (base_url, model_name) 后续调用零额外开销。
        """
        try:
            return await self._call_with_retry(do_call, label=label)
        except APIStatusError as e:
            if e.status_code == 400 and _is_max_tokens_unsupported_error(e):
                if not _is_max_completion_tokens_required(self.base_url, self.model_name):
                    _mark_max_completion_tokens_required(self.base_url, self.model_name)
                    logger.warning(
                        f"{label}: 模型 {self.model_name} 不支持 max_tokens, "
                        f"已切换为 max_completion_tokens 并自动重试"
                    )
                    return await self._call_with_retry(do_call, label=label)
            raise

    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        seed: Optional[int] = None,
    ) -> str:
        """发送聊天请求
        
        Args:
            user_message: 用户消息
            system_message: 系统提示（可选）
            temperature: 采样温度（0-1），越低越确定性
            max_tokens: 最大生成 token 数
            seed: S.3.1 显式 seed；None 时按 contextvar 派生（LLM_SEED_ENABLED=false 时永远 None）
            
        Returns:
            str: 模型响应文本
        """
        if not self.api_key:
            raise ValueError("云端API密钥未设置，请在.env文件中配置 CLOUD_API_KEY")
        
        messages = []
        
        if system_message:
            messages.append({"role": "system", "content": system_message})
        
        messages.append({"role": "user", "content": user_message})

        temperature = self._resolve_temperature(temperature)
        effective_seed = self._resolve_seed(seed)

        logger.debug(
            f"发送云端LLM请求: model={self.model_name}, "
            f"temp={temperature}, max_tokens={max_tokens}, seed={effective_seed}"
        )
        
        t0 = time.monotonic()
        try:
            async def _do_call():
                from app.core.ai.thinking_params import build_client_extra_kwargs, seed_allowed
                _kwargs: dict = dict(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                )
                _kwargs.update(_apply_token_limit_kwargs(self.base_url, self.model_name, max_tokens))
                _kwargs.update(build_client_extra_kwargs(self))
                if effective_seed is not None and seed_allowed(self):
                    _kwargs["seed"] = effective_seed
                return await self._client.chat.completions.create(**_kwargs)

            response = await self._call_completions_create(_do_call, label=f"chat[{self.model_name}]")
            content = response.choices[0].message.content
            finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
            latency = time.monotonic() - t0
            self._last_usage = None
            pt = ct = 0
            if hasattr(response, "usage") and response.usage:
                pt = response.usage.prompt_tokens or 0
                ct = response.usage.completion_tokens or 0
                self._last_usage = {
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": response.usage.total_tokens,
                }

            self._last_finish_reason = finish_reason

            if content is None:
                empty_return_total.labels(flow="all", purpose="chat").inc()
                logger.warning(
                    f"云端LLM响应content=None: model={self.model_name}, "
                    f"finish_reason={finish_reason}, latency={latency:.1f}s"
                )
                record_llm_call(self.model_name, "chat", "empty", latency, pt, ct)
                return ""

            record_llm_call(self.model_name, "chat", "success", latency, pt, ct)
            logger.debug(f"云端LLM响应: {len(content)} 字符, {latency:.1f}s")

            return content
            
        except Exception as e:
            latency = time.monotonic() - t0
            self._last_usage = None
            self._last_finish_reason = None
            record_llm_call(self.model_name, "chat", "error", latency)
            logger.error(f"云端LLM调用失败: model={self.model_name}, {e}")
            raise
    
    async def chat_with_images(
        self,
        text_message: str,
        image_data_list: list[str],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        seed: Optional[int] = None,
    ) -> str:
        """发送多模态聊天请求（文本+图片）
        
        使用 OpenAI 兼容的多模态消息格式，将图片以 base64 编码发送给VL模型。
        Ollama 和主流云端API（OpenAI/Qwen/DeepSeek）均兼容此格式。
        
        Args:
            text_message: 文本提示
            image_data_list: base64编码的图片列表（不含data:前缀）
            system_message: 系统提示（可选）
            temperature: 采样温度
            max_tokens: 最大生成token数
            
        Returns:
            str: 模型响应文本
        """
        if not self.api_key:
            raise ValueError("API密钥未设置")
        
        messages: list[dict] = []
        
        if system_message:
            messages.append({"role": "system", "content": system_message})
        
        # 构建多模态content: 先文本后图片
        content: list[dict] = [{"type": "text", "text": text_message}]

        payload_result = build_vision_image_payloads(image_data_list)
        for skipped in payload_result.skipped:
            logger.warning(
                f"VL图片跳过: model={self.model_name}, "
                f"index={skipped.index}, reason={skipped.reason}, "
                f"format={skipped.source_format}, "
                f"size={skipped.width}x{skipped.height}, bytes={skipped.byte_size}"
            )
        if not payload_result.payloads:
            logger.warning(
                f"多模态LLM请求无有效图片: model={self.model_name}, "
                f"images={len(image_data_list)}"
            )
            return ""

        for payload in payload_result.payloads:
            content.append({
                "type": "image_url",
                "image_url": {"url": payload.data_url}
            })
        
        messages.append({"role": "user", "content": content})

        temperature = self._resolve_temperature(temperature)
        effective_seed = self._resolve_seed(seed)

        logger.debug(
            f"发送多模态LLM请求: model={self.model_name}, "
            f"images={len(payload_result.payloads)}/{len(image_data_list)}, "
            f"temp={temperature}, seed={effective_seed}"
        )
        
        t0 = time.monotonic()
        try:
            async def _do_vision_call():
                from app.core.ai.thinking_params import build_client_extra_kwargs, seed_allowed
                _kwargs: dict = dict(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                )
                _kwargs.update(_apply_token_limit_kwargs(self.base_url, self.model_name, max_tokens))
                _kwargs.update(build_client_extra_kwargs(self))
                if effective_seed is not None and seed_allowed(self):
                    _kwargs["seed"] = effective_seed
                return await self._client.chat.completions.create(**_kwargs)

            response = await self._call_completions_create(_do_vision_call, label=f"vision[{self.model_name}]")
            
            latency = time.monotonic() - t0
            raw_content = response.choices[0].message.content
            finish_reason = getattr(response.choices[0], "finish_reason", "unknown")

            if raw_content is None:
                if finish_reason != "length":
                    empty_return_total.labels(flow="all", purpose="vision").inc()
                    logger.warning(
                        f"多模态LLM响应content=None: model={self.model_name}, "
                        f"finish_reason={finish_reason}"
                    )
                    record_llm_call(self.model_name, "vision", "empty", latency)
                    return ""
                raw_len = 0
                result = ""
                logger.warning(
                    f"多模态LLM响应content=None+截断: model={self.model_name}, "
                    f"将尝试扩容重试"
                )
            else:
                raw_len = len(raw_content)
                result = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()

                if raw_len > 0 and not result:
                    logger.warning(
                        f"多模态LLM响应被think标签完全消耗: model={self.model_name}, "
                        f"raw_len={raw_len}, finish_reason={finish_reason}, "
                        f"preview={raw_content[:150]}"
                    )
                elif not result:
                    logger.warning(
                        f"多模态LLM响应为空字符串: model={self.model_name}, "
                        f"finish_reason={finish_reason}"
                    )
                else:
                    logger.debug(f"多模态LLM响应: {len(result)} 字符, {latency:.1f}s")

            _VL_LENGTH_RETRY_CEILING = 4096
            if not result and finish_reason == "length" and max_tokens < _VL_LENGTH_RETRY_CEILING:
                retry_tokens = min(max_tokens * 2, _VL_LENGTH_RETRY_CEILING)
                logger.info(
                    f"VL响应截断重试: finish_reason=length+内容为空, "
                    f"max_tokens {max_tokens}->{retry_tokens}, model={self.model_name}"
                )
                try:
                    async def _do_vision_retry():
                        from app.core.ai.thinking_params import (
                            build_client_extra_kwargs,
                            seed_allowed,
                        )
                        _retry_kwargs: dict = dict(
                            model=self.model_name,
                            messages=messages,
                            temperature=temperature,
                        )
                        _retry_kwargs.update(
                            _apply_token_limit_kwargs(self.base_url, self.model_name, retry_tokens)
                        )
                        _retry_kwargs.update(build_client_extra_kwargs(self))
                        if effective_seed is not None and seed_allowed(self):
                            _retry_kwargs["seed"] = effective_seed
                        return await self._client.chat.completions.create(**_retry_kwargs)
                    retry_resp = await self._call_completions_create(
                        _do_vision_retry, label=f"vision_retry[{self.model_name}]"
                    )
                    retry_raw = retry_resp.choices[0].message.content
                    if retry_raw:
                        result = re.sub(
                            r'<think>.*?</think>', '', retry_raw, flags=re.DOTALL
                        ).strip()
                    if result:
                        logger.info(f"VL截断重试成功: {len(result)}字符, model={self.model_name}")
                    else:
                        retry_fr = getattr(retry_resp.choices[0], "finish_reason", "unknown")
                        logger.warning(
                            f"VL截断重试仍为空: finish_reason={retry_fr}, "
                            f"raw_len={len(retry_raw) if retry_raw else 0}"
                        )
                except Exception as retry_err:
                    logger.warning(f"VL截断重试异常(不影响主流程): {retry_err}")

            record_llm_call(
                self.model_name, "vision",
                "success" if result else "empty", latency,
            )
            return result
            
        except Exception as e:
            latency = time.monotonic() - t0
            record_llm_call(self.model_name, "vision", "error", latency)
            logger.error(f"多模态LLM调用失败: model={self.model_name}, {e}")
            raise

    async def _chat_with_response_format(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: Optional[dict] = None,
        *,
        seed: Optional[int] = None,
    ) -> str:
        """B2.7 — 带 response_format 的 chat 调用（约束解码路径）。

        provider 不支持时 catch 错误并降级到普通 chat。
        """
        if not self.api_key:
            raise ValueError("云端API密钥未设置")

        messages: list[dict] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_message})

        temperature = self._resolve_temperature(temperature)
        effective_seed = self._resolve_seed(seed)

        from app.core.ai.dispatcher import detect_provider
        from app.core.observability.metrics import constrained_decoding_attempts_total
        provider = detect_provider(self.base_url)

        # 修复 + 进阶：粘性降级
        # 1) 实例级标志（原有，防单 client 内部重复试错）
        # 2) (base_url, model_name) 进程级缓存（新增）——
        #    dispatcher 多 key 通道每个 key 是独立 CloudClient 实例，
        #    只有进程级缓存能避免"一个 key 被刺 400 后，其他 key 又被刺一遍"。
        if self._json_schema_unsupported or _is_response_format_unsupported(
            self.base_url, self.model_name
        ):
            # 同步实例级标志，后续调用直接短路
            self._json_schema_unsupported = True
            constrained_decoding_attempts_total.labels(
                flow="all", provider=provider, outcome="fallback",
            ).inc()
            return await self.chat(
                user_message=user_message,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                seed=effective_seed,
            )

        t0 = time.monotonic()
        try:
            async def _do_cd_call():
                from app.core.ai.thinking_params import build_client_extra_kwargs, seed_allowed
                kwargs: dict = dict(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                )
                kwargs.update(_apply_token_limit_kwargs(self.base_url, self.model_name, max_tokens))
                kwargs.update(build_client_extra_kwargs(self))
                if response_format:
                    kwargs["response_format"] = response_format
                if effective_seed is not None and seed_allowed(self):
                    kwargs["seed"] = effective_seed
                return await self._client.chat.completions.create(**kwargs)

            response = await self._call_completions_create(
                _do_cd_call, label=f"chat_cd[{self.model_name}]",
            )
            content = response.choices[0].message.content or ""
            # 与 chat 对齐，chat_json 的 length+空响应扩容指纹依赖此值
            self._last_finish_reason = getattr(
                response.choices[0], "finish_reason", "unknown"
            )
            latency = time.monotonic() - t0
            self._last_usage = None
            if hasattr(response, "usage") and response.usage:
                self._last_usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens,
                }
            from app.core.ai.metrics import record_llm_call
            record_llm_call(self.model_name, "chat_cd", "success", latency,
                            self._last_usage.get("prompt_tokens", 0) if self._last_usage else 0,
                            self._last_usage.get("completion_tokens", 0) if self._last_usage else 0)
            constrained_decoding_attempts_total.labels(
                flow="all", provider=provider, outcome="success",
            ).inc()
            return content

        except Exception as e:
            latency = time.monotonic() - t0
            self._last_finish_reason = None
            err_msg = str(e).lower()
            is_unsupported = any(kw in err_msg for kw in (
                "response_format", "json_schema", "unsupported", "not supported",
            ))

            # provider 拒绝的是"我们发出的 schema"而非"response_format 参数"时，
            # 绝不能写粘性缓存——那会把一个支持约束解码的健康通道在整个进程生命周期内
            # 打成"不支持"。这类错误是我方缺陷，必须持续可见（ERROR 级 + 独立 outcome），
            # 重复报错正是期望行为，不应被缓存掩盖。
            from app.core.ai.constrained_decoding import is_schema_rejection_error
            if is_unsupported and is_schema_rejection_error(err_msg):
                constrained_decoding_attempts_total.labels(
                    flow="all", provider=provider, outcome="schema_rejected",
                ).inc()
                logger.error(
                    f"约束解码 schema 被拒(我方 schema 不合规, 未标记 provider 能力): "
                    f"model={self.model_name} err={str(e)[:200]}"
                )
                raise UnsupportedResponseFormatError(str(e)) from e

            if is_unsupported:
                # 同时落到实例级与进程级缓存，保证同 provider/model 的其他 key
                # 通道后续不再重复发送 response_format 请求。
                self._json_schema_unsupported = True
                _mark_response_format_unsupported(self.base_url, self.model_name)
                constrained_decoding_attempts_total.labels(
                    flow="all", provider=provider, outcome="fallback",
                ).inc()
                logger.warning(
                    f"约束解码不支持, 标记 (base_url, model)={self.base_url}::{self.model_name} "
                    f"进程级不支持, 上抛 UnsupportedResponseFormatError 给 dispatcher: "
                    f"err={str(e)[:120]}"
                )
                # 不在此方法内降级为 self.chat() —— 把信号显式上抛，
                # dispatcher 可以按策略（跨组 / 本实例回退）处置；
                # 上层 chat_json 仍会捕获并回退为普通 chat，保持对外契约不变。
                raise UnsupportedResponseFormatError(str(e)) from e
            raise

    async def chat_json(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        json_schema: Optional[dict] = None,
        seed: Optional[int] = None,
        propagate_unsupported: bool = False,
        allow_plain_fallback: bool = True,
        raw_response_sink: Optional[list] = None,
    ) -> dict:
        """发送聊天请求并解析 JSON 响应
 
 Args:
 user_message: 用户消息
 system_message: 系统提示（可选）
 temperature: 采样温度
 max_tokens: 最大生成 token 数
 json_schema: OpenAI 兼容 response_format（B2.7 新增），None 走现有路径
 seed: S.3.1 显式 seed；None 时按 contextvar 派生
 propagate_unsupported: True 时把 response_format 能力不兼容信号交给上层调度器处理
 raw_response_sink: 非 None 时追加原始响应文本（：
 供 chat_with_validation 在校验失败时做多候选 JSON 回退）
 
 Returns:
 dict: 解析后的 JSON 对象
 
 Raises:
 EmptyLLMResponseError: 清洗后无可解析文本（空响应 / think 洗空）
 json.JSONDecodeError: JSON 解析失败（有正文但非法）
 """
        # length 撞顶是输出预算故障；空响应与截断非空都先同通道扩容一次。
        _JSON_LENGTH_RETRY_CEILING = 8192
        attempt_max = max_tokens
        escalated = False
        while True:
            if json_schema is not None:
                try:
                    response = await self._chat_with_response_format(
                        user_message=user_message,
                        system_message=system_message,
                        temperature=temperature,
                        max_tokens=attempt_max,
                        response_format=json_schema,
                        seed=seed,
                    )
                except UnsupportedResponseFormatError:
                    # P0-D: 调用方声明 fail-closed 时禁止本实例静默降级
                    if propagate_unsupported or not allow_plain_fallback:
                        raise
                    # 对外契约向后兼容：provider 首次报告不支持 response_format 时，
                    # 本实例直接回退到普通 chat，避免调用方因此类 400 直接失败。
                    # 进程级缓存已在 _chat_with_response_format 中写入，
                    # 后续同 provider/model 的调用会直接走上面的短路分支。
                    response = await self.chat(
                        user_message=user_message,
                        system_message=system_message,
                        temperature=temperature,
                        max_tokens=attempt_max,
                        seed=seed,
                    )
            else:
                response = await self.chat(
                    user_message=user_message,
                    system_message=system_message,
                    temperature=temperature,
                    max_tokens=attempt_max,
                    seed=seed,
                )

            if raw_response_sink is not None:
                raw_response_sink.append(response)

            raw_text = response or ""
            raw_len = len(raw_text)
            content, think_consumed = strip_think_and_code_fences(raw_text)
            finish_reason = getattr(self, "_last_finish_reason", None)

            # 清洗后为空是通道/模型可靠性问题，不得伪装成 JSONDecodeError
            # （否则 dispatcher 按语义错误不换通道，提取层同模型打满重试）。
            if not content:
                logger.warning(
                    f"chat_json 空响应: model={self.model_name}, "
                    f"finish_reason={finish_reason}, raw_len={raw_len}, "
                    f"think_consumed={think_consumed}"
                )
                if (
                    not escalated
                    and finish_reason == "length"
                    and attempt_max < _JSON_LENGTH_RETRY_CEILING
                ):
                    escalated = True
                    attempt_max = min(attempt_max * 2, _JSON_LENGTH_RETRY_CEILING)
                    logger.info(
                        f"chat_json 空响应截断扩容: model={self.model_name}, "
                        f"max_tokens -> {attempt_max}"
                    )
                    continue
                raise EmptyLLMResponseError(
                    f"LLM 返回空响应: model={self.model_name}, "
                    f"finish_reason={finish_reason}, raw_len={raw_len}, "
                    f"think_consumed={think_consumed}",
                    model_name=self.model_name,
                    finish_reason=finish_reason,
                    raw_len=raw_len,
                    think_consumed=think_consumed,
                )

            content = self._extract_first_json_payload(content)
            logger.debug(f"解析JSON内容: {content[:200]}...")
            try:
                return json.loads(content)
            except json.JSONDecodeError as original_err:
                if (
                    not escalated
                    and finish_reason == "length"
                    and attempt_max < _JSON_LENGTH_RETRY_CEILING
                ):
                    escalated = True
                    attempt_max = min(attempt_max * 2, _JSON_LENGTH_RETRY_CEILING)
                    logger.info(
                        f"chat_json 截断扩容: model={self.model_name}, "
                        f"max_tokens -> {attempt_max}"
                    )
                    continue
                if escalated and finish_reason == "length":
                    raise LlmContentValidationError(
                        f"chat_json 扩容后仍截断: model={self.model_name}, "
                        f"max_tokens={attempt_max}",
                        suggested_max_tokens=attempt_max,
                    ) from original_err
                repaired = self._try_repair_json(content)
                if repaired is not None:
                    return repaired
                get_attempt_recorder().amend_last_attempt(LlmOutcome.INVALID_JSON)
                raise original_err

    @staticmethod
    def _extract_first_json_payload(text: str) -> str:
        """提取响应中的首个完整 JSON 对象或数组。

 必须使用标准 JSON 词法分析器，不能用裸括号计数：
 招标语料中字符串值经常含 `[]`/`{}`，手写计数器会把字符串内符号误判为结构。

 关键语义保护:
 当首个开口符号是 `[` 但 raw_decode 失败时（典型为 max_tokens 截断的数组），
 必须保留 `[` 起始的原文交由 _try_repair_json 抢救，禁止退化为后续第一个完整
 `{...}` 对象——否则数组语义会被静默降级为单条对象，丢失 N-1 条已生成的元素。

 对称保护:
 首个开口符号是 `{` 且 raw_decode 失败时（典型为 max_tokens 截断的顶层对象），
 同样必须保留 `{` 起始的原文交由 _try_repair_json 的对象截断修复（策略3）抢救。
 禁止在此处直接降级为"后续第一个完整 {...}"——那可能是答案的内嵌小对象
 （如 Prompt 注入的评分基准 {"expected_total": 100.0}），合法 JSON 会静默
 绕过截断扩容与对象修复，比解析失败更危险。原"内嵌完整对象扫描"降级
 改为 _try_repair_json 的最后策略（_rescue_first_nested_json），仅在顶层
 修复全部失败（如首个 `{` 是非 JSON 的占位文本）时启用。
 """
        stripped = text.strip()
        if not stripped:
            return stripped

        decoder = json.JSONDecoder()

        first_open_idx = -1
        for idx, char in enumerate(stripped):
            if char in "[{":
                first_open_idx = idx
                break

        if first_open_idx < 0:
            return stripped

        try:
            _, end = decoder.raw_decode(stripped[first_open_idx:])
            return stripped[first_open_idx:first_open_idx + end]
        except json.JSONDecodeError:
            pass

        # 截断保护（数组 / 对象对称化）：
        # 保留开口符号起始的原文，让 _try_repair_json 优先抢救截断的顶层结构。
        return stripped[first_open_idx:]

    def _try_repair_json(self, content: str) -> list | dict | None:
        """多策略 JSON 修复,按成本从低到高依次尝试。

 策略顺序:
 1. 清理尾随逗号 (] 或,})
 2. 修复未转义的控制字符 (换行/制表符嵌入字符串值)
 3. 截断修复: 数组/对象被 max_tokens 截断时, 丢弃残缺尾部保留已完整部分
 4. 错误位置前截断: 在 JSONDecodeError 指示的出错位置前截断, 抢救前段
 5. 内嵌对象降级: 顶层修复全部失败(如首个 { 是非 JSON 占位文本)时,
 扫描文本中首个完整内嵌 {...} 对象。必须放在最后——截断顶层对象场景下
 内嵌对象是答案片段, 只有顶层无法抢救时才允许降级。
 """
        stripped = content.strip()
        if not stripped:
            return None

        # --- 策略 1: 尾随逗号 ---
        candidate = re.sub(r',\s*([}\]])', r'\1', stripped)
        result = self._safe_json_loads(candidate)
        if result is not None:
            logger.info("JSON修复成功(策略1: 清理尾随逗号)")
            return result

        # --- 策略 2: 未转义控制字符 ---
        candidate = self._escape_control_chars(stripped)
        result = self._safe_json_loads(candidate)
        if result is not None:
            logger.info("JSON修复成功(策略2: 转义控制字符)")
            return result

        # --- 策略 3: 尾部截断修复 (原有逻辑) ---
        result = self._repair_truncated_array(stripped)
        if result is not None:
            return result

        # --- 策略 4: 错误位置前截断抢救 ---
        result = self._repair_before_error(stripped)
        if result is not None:
            return result

        # 策略 5: 内嵌完整对象降级(原 _extract_first_json_payload 降级逻辑) ---
        from app.core.ai.json_payload_recovery import rescue_first_nested_object

        fragment = rescue_first_nested_object(stripped)
        if fragment is not None:
            result = self._safe_json_loads(fragment)
            if isinstance(result, dict):
                self._record_json_repair(
                    repair_type="nested_object_rescue",
                    source="json_repair_nested_object",
                    original_length=len(stripped),
                    recovered_elements=len(result),
                )
                logger.info(
                    f"JSON修复成功(策略5: 内嵌对象降级): 顶层不可抢救, "
                    f"取首个完整内嵌对象 ({len(result)} 个字段)"
                )
                return result

        return None

    @staticmethod
    def _safe_json_loads(text: str) -> list | dict | None:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (list, dict)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _escape_control_chars(text: str) -> str:
        """转义 JSON 字符串值内的裸控制字符(换行/制表)。

        只处理双引号包裹的字符串内部, 不影响 JSON 结构字符。
        """
        def _escape_match(m: re.Match) -> str:
            inner = m.group(1)
            inner = inner.replace('\n', '\\n')
            inner = inner.replace('\r', '\\r')
            inner = inner.replace('\t', '\\t')
            return '"' + inner + '"'

        return re.sub(
            r'"((?:[^"\\]|\\.)*?(?:\n|\r|\t)(?:[^"\\]|\\.)*?)"',
            _escape_match,
            text,
        )

    def _repair_truncated_array(self, stripped: str) -> list | dict | None:
        """修复被 max_tokens 截断的 JSON 数组或对象。"""
        if stripped.startswith("["):
            last_item_end = self._find_last_complete_array_item_end(stripped)
            if last_item_end <= 0:
                return None

            candidate = stripped[:last_item_end + 1].rstrip().rstrip(",") + "]"
            result = self._safe_json_loads(candidate)
            if isinstance(result, list) and len(result) > 0:
                self._record_json_repair(
                    repair_type="truncated_array",
                    source="json_repair_truncated",
                    original_length=len(stripped),
                    recovered_elements=len(result),
                )
                logger.info(
                    f"JSON修复成功(策略3: 截断修复): 抢救 {len(result)} 个元素 "
                    f"(原始 {len(stripped)} chars, 截取至 {last_item_end + 1})"
                )
                return result

        if stripped.startswith("{"):
            candidate = self._build_repaired_object_candidate(stripped)
            if candidate is None:
                return None
            result = self._safe_json_loads(candidate)
            if isinstance(result, dict) and len(result) > 0:
                self._record_json_repair(
                    repair_type="truncated_object",
                    source="json_repair_truncated_object",
                    original_length=len(stripped),
                    recovered_elements=len(result),
                )
                logger.info(
                    f"JSON修复成功(策略3: 对象截断修复): 抢救 {len(result)} 个字段 "
                    f"(原始 {len(stripped)} chars, 修复后 {len(candidate)} chars)"
                )
                return result
        return None

    def _repair_before_error(self, stripped: str) -> list | dict | None:
        """在解析出错位置之前截断, 抢救已完整的前段元素。"""
        if not stripped.startswith(("[", "{")):
            return None

        try:
            json.loads(stripped)
            return None
        except json.JSONDecodeError as e:
            error_pos = e.pos or 0

        if error_pos < 2:
            return None

        prefix = stripped[:error_pos]
        if stripped.startswith("["):
            last_item_end = self._find_last_complete_array_item_end(prefix)
            if last_item_end <= 0:
                return None

            candidate = prefix[:last_item_end + 1].rstrip().rstrip(",") + "]"
            result = self._safe_json_loads(candidate)
            if isinstance(result, list) and len(result) > 0:
                self._record_json_repair(
                    repair_type="before_error",
                    source="json_repair_before_error",
                    original_length=len(stripped),
                    recovered_elements=len(result),
                )
                logger.info(
                    f"JSON修复成功(策略4: 错误位置前截断): 抢救 {len(result)} 个元素 "
                    f"(错误位置 char {error_pos}, 截取至 {last_item_end + 1})"
                )
                return result

        if stripped.startswith("{"):
            candidate = self._build_repaired_object_candidate(prefix)
            if candidate is None:
                return None
            result = self._safe_json_loads(candidate)
            if isinstance(result, dict) and len(result) > 0:
                self._record_json_repair(
                    repair_type="before_error_object",
                    source="json_repair_before_error_object",
                    original_length=len(stripped),
                    recovered_elements=len(result),
                )
                logger.info(
                    f"JSON修复成功(策略4: 对象错误位置前截断): 抢救 {len(result)} 个字段 "
                    f"(错误位置 char {error_pos}, 修复后 {len(candidate)} chars)"
                )
                return result
        return None

    @staticmethod
    def _find_last_complete_array_item_end(text: str) -> int:
        """返回根数组中最后一个完整对象元素的结束位置。"""
        in_string = False
        escaped = False
        bracket_depth = 0
        brace_depth = 0
        last_item_end = -1

        for idx, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                if brace_depth > 0:
                    brace_depth -= 1
                if bracket_depth == 1 and brace_depth == 0:
                    last_item_end = idx

        return last_item_end

    @staticmethod
    def _find_last_top_level_object_comma(text: str) -> int:
        """返回根对象内最后一个顶层逗号位置，忽略字符串和嵌套结构。"""
        in_string = False
        escaped = False
        brace_depth = 0
        bracket_depth = 0
        last_comma = -1

        for idx, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif char == "," and brace_depth == 1 and bracket_depth == 0:
                last_comma = idx

        return last_comma

    def _build_repaired_object_candidate(self, text: str) -> str | None:
        """丢弃根对象中残缺的最后一个字段，保留已完整字段。"""
        comma_pos = self._find_last_top_level_object_comma(text)
        if comma_pos <= 1:
            return None

        candidate = text[:comma_pos].rstrip().rstrip(",") + "}"
        return candidate if self._safe_json_loads(candidate) is not None else None

    @staticmethod
    def _record_json_repair(
        *,
        repair_type: str,
        source: str,
        original_length: int,
        recovered_elements: int,
    ) -> None:
        json_repair_total.labels(repair_type=repair_type, flow="all").inc()
        from app.core.contracts.degradation_signal import (
            DegradationSignal,
            push_signal,
        )
        push_signal(DegradationSignal(
            source=source,
            flow="all",
            repair_type=repair_type,
            original_length=original_length,
            recovered_elements=recovered_elements,
        ))
    
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
        propagate_unsupported: bool = False,
        allow_plain_fallback: bool = True,
    ) -> BaseModel:
        """发送聊天请求并使用 Pydantic 校验响应
 
 Args:
 user_message: 用户消息
 response_model: Pydantic 模型类
 system_message: 系统提示（可选）
 temperature: 采样温度
 max_tokens: 最大生成 token 数
 max_retries: 最大重试次数
 json_schema: OpenAI 兼容 response_format（B2.7 新增），None 走现有路径
 propagate_unsupported: True 时把 response_format 能力不兼容信号
 上抛给调度器跨组处置（：对齐 chat_json 的 语义，
 避免严格 schema 任务在不支持约束解码的模型上静默退化为自由文本）
 
 Returns:
 BaseModel: 校验后的响应对象
 
 Raises:
 ValueError: 多次重试后仍无法获得有效响应
 UnsupportedResponseFormatError: propagate_unsupported=True 且
 provider/model 不支持 response_format 时原样上抛
 """
        _VALIDATION_TOKEN_CEILING = 8192

        last_error = None
        current_message = user_message
        current_max_tokens = max_tokens
        
        for attempt in range(max_retries):
            raw_sink: list = []
            try:
                data = await self.chat_json(
                    user_message=current_message,
                    system_message=system_message,
                    temperature=temperature,
                    max_tokens=current_max_tokens,
                    json_schema=json_schema,
                    propagate_unsupported=propagate_unsupported,
                    allow_plain_fallback=allow_plain_fallback,
                    raw_response_sink=raw_sink,
                )
                
                # Pydantic 校验
                return response_model(**data)
                
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败 (attempt {attempt + 1}): {e}")
                last_error = e
                is_truncated = "Unterminated" in str(e) or "Expecting" in str(e)
                if is_truncated:
                    if current_max_tokens < _VALIDATION_TOKEN_CEILING:
                        new_tokens = min(current_max_tokens * 2, _VALIDATION_TOKEN_CEILING)
                        logger.info(
                            f"JSON截断自动扩容: max_tokens {current_max_tokens}->{new_tokens}, "
                            f"model={self.model_name}"
                        )
                        current_max_tokens = new_tokens
                    current_message = (
                        user_message +
                        "\n\n你的上次输出被截断导致JSON不完整。请精简输出: "
                        "每条finding的detail控制在50字以内，减少quote_a/quote_b长度。"
                    )
                else:
                    current_message = user_message + "\n\n请确保输出为有效的 JSON 格式，不要包含其他内容。"
                
            except ValidationError as e:
                # 首个 payload 可能只是模型回显的 Prompt 内 JSON 常量，
                # 真实答案在同一响应的后续候选中；先做候选回退，全部失败才计失败。
                raw_text = raw_sink[-1] if raw_sink else ""
                from app.core.ai.json_payload_recovery import recover_validated_payload
                recovered = recover_validated_payload(
                    raw_text, response_model, skip_payload=data,
                )
                if recovered is not None:
                    return recovered
                get_attempt_recorder().amend_last_attempt(LlmOutcome.SCHEMA_INVALID)
                logger.warning(
                    f"Pydantic 校验失败 (attempt {attempt + 1}): {e}; "
                    f"raw_head={raw_text[:200]!r}"
                )
                last_error = e
                error_details = str(e)
                current_message = (
                    user_message +
                    f"\n\n上次输出校验失败: {error_details}\n"
                    "请修正以下问题:\n"
                    "1. 所有必填字段不得为null，如无法确认则移除该条目\n"
                    "2. 字符串类型字段不得填写null，应填实际值或移除整个条目\n"
                    "3. 确保输出为有效的JSON格式"
                )
            
            # 指数退避
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        
        raise LlmContentValidationError(
            f"云端LLM调用 {max_retries} 次后仍无法获得有效响应: {last_error}",
            # 本通道内发生过截断扩容时, 把扩容后的预算随失败信号带出,
            # 供 dispatcher 跨组换模型时续用（current > 初始值即为扩容证据）。
            suggested_max_tokens=(
                current_max_tokens if current_max_tokens > max_tokens else None
            ),
        )
    
    async def health_check(self) -> bool:
        """检查云端服务是否可用
        
        Returns:
            bool: 服务是否可用
        """
        if not self.api_key:
            logger.warning("云端API密钥未设置")
            return False
        
        try:
            response = await self.chat(
                user_message="你好",
                max_tokens=10
            )
            return bool(response)
        except Exception as e:
            logger.warning(f"云端服务健康检查失败: {e}")
            return False


# 全局客户端实例（单例模式）
_cloud_client: Optional[CloudClient] = None


def get_cloud_client() -> CloudClient:
    """获取全局云端客户端实例
    
    使用单例模式避免重复创建客户端。
    
    Returns:
        CloudClient: 云端客户端实例
    """
    global _cloud_client
    
    if _cloud_client is None:
        _cloud_client = CloudClient()
    
    return _cloud_client


def reset_cloud_client() -> None:
    """重置全局客户端实例（用于测试或配置更新后）"""
    global _cloud_client
    _cloud_client = None


# ==================== 本地客户端（Ollama）====================

# 全局本地客户端实例
_local_client: Optional[CloudClient] = None


def get_local_client() -> CloudClient:
    """获取本地LLM客户端实例（Ollama）
    
    使用与云端相同的CloudClient类，但配置为本地Ollama服务。
    Ollama兼容OpenAI API格式。
    
    Returns:
        CloudClient: 配置为本地Ollama的客户端实例
    """
    global _local_client
    
    if _local_client is None:
        _local_client = CloudClient(
            base_url=settings.LLM_API_BASE,
            api_key="ollama",  # Ollama不需要真实密钥
            model_name=settings.LLM_MODEL_NAME,
            timeout=300.0  # 本地推理需要更长时间
        )
        logger.info(
            f"本地LLM客户端初始化: base_url={settings.LLM_API_BASE}, "
            f"model={settings.LLM_MODEL_NAME}"
        )
    
    return _local_client


def reset_local_client() -> None:
    """重置本地客户端实例"""
    global _local_client
    _local_client = None
