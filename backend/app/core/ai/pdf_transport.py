"""OpenAI 兼容协议下 "chat 直传 PDF" 的多方言传输适配。

职责: 按能力档案解析可承接 PDF 的通道目标, 并以对应方言完成单次
      "PDF 附件 -> 模型文本回复" 调用(消息内 file 块 / 方舟 file_url /
      DashScope fileid 两步式 / Kimi·阶跃式 extract-text), 附 trace 记账。
不做: 不做门禁/JSONL解析/采纳(评分通道职责)、不参与常规 chat 调度
      (dispatcher 职责)、不持久化能力档案(探针脚本职责)。

已知边界: dashscope_fileid 与 extract_text 方言由服务商侧解析为文本,
页码边界可能丢失, source_page 精度低于 file 块方言(多标段回填受影响)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from loguru import logger
from openai import APIStatusError, AsyncOpenAI

from app.core.ai.temperature_params import (
    TemperatureSettings,
    resolve_request_temperature,
    temperature_settings_from_config,
)

PdfDialect = Literal["openai_file", "ark_file_url", "dashscope_fileid", "extract_text"]

# 视为"该通道不支持PDF输入"的HTTP状态码(参数校验/端点缺失/载荷超限/格式不受理)
_UNSUPPORTED_STATUS = {400, 404, 413, 415, 422}

# 已知厂商主机 -> 默认方言(能力档案为 auto 时的推断兜底,);
# 声明值(探针/管理员)优先, 推断错误由运行时 unsupported 学习纠正
_HOST_DIALECT_HINTS: tuple[tuple[str, "PdfDialect"], ...] = (
    ("moonshot", "extract_text"),
    ("kimi", "extract_text"),
    ("stepfun", "extract_text"),
    ("volces.com", "ark_file_url"),
)

# 会话级不支持标记: 运行时实测 unsupported 后本进程内跳过, 重启即重试
_session_unsupported: set[str] = set()

# 会话级"该目标拒绝temperature参数"记忆(如 kimi-k3 仅接受服务端默认,),
# 避免每次调用都先白付一次 400
_session_no_temperature: set[str] = set()


class PdfInputUnsupported(Exception):
    """目标通道不支持当前 PDF 输入方言(调用方应降级到下一通道/VLM)。"""


class EmptyPdfCompletion(Exception):
    """HTTP 成功但 content/reasoning 均为空(调用方应降级下一目标, 不得拉黑通道)。"""


@dataclass(frozen=True)
class PdfTransportTarget:
    """一个可尝试 PDF 直传的通道目标(凭据已解密)。"""

    config_id: str
    label: str
    api_base: str
    api_key: str
    model_name: str
    dialect: PdfDialect
    timeout: float
    temperature_settings: Optional[TemperatureSettings] = None
    capability_profile: Optional[Any] = None

    @property
    def session_key(self) -> str:
        return f"{self.api_base}|{self.model_name}|{self.dialect}"


def _make_target(
    config,
    *,
    dialect: PdfDialect,
    timeout: float,
    model_name: Optional[str] = None,
    config_id: Optional[str] = None,
) -> PdfTransportTarget:
    """从用户模型配置组装目标, 带上温度约束与能力档案。"""
    name = model_name or config.model_name
    return PdfTransportTarget(
        config_id=config_id or config.id,
        label=f"{name}@{_host(config.api_base)}",
        api_base=config.api_base,
        api_key=_first_key(config),
        model_name=name,
        dialect=dialect,
        timeout=timeout,
        temperature_settings=temperature_settings_from_config(config),
        capability_profile=getattr(config, "capabilities", None),
    )


def _first_key(config) -> str:
    """取配置的首个可用 API Key(get_all_keys 已含主密钥优先)。"""
    keys = config.get_all_keys()
    if keys:
        return keys[0]
    if getattr(config, "config_type", "") == "local":
        return "ollama"
    return ""


def _host(api_base: str) -> str:
    try:
        return (urlparse(api_base).hostname or "").lower()
    except Exception:  # noqa: BLE001 - 畸形URL按空主机处理
        return ""


def mark_target_unsupported(target: PdfTransportTarget) -> None:
    """会话级标记目标不支持(fail-fast, 进程重启后重试)。"""
    _session_unsupported.add(target.session_key)


def _resolve_dialect(config) -> str:
    """配置的有效PDF方言: 声明值优先; auto 时按已知厂商主机推断。

 返回 "auto" 表示未知厂商无法推断, 该配置不参与PDF直传。
 """
    declared = getattr(config.capabilities, "pdf_file_input", "auto") or "auto"
    if declared != "auto":
        return declared  # 含显式 none(管理员排除)
    host = _host(config.api_base)
    for hint, dialect in _HOST_DIALECT_HINTS:
        if hint in host:
            return dialect
    if "dashscope" in host and str(config.model_name or "").startswith(
        ("qwen-long", "qwen-doc")
    ):
        return "dashscope_fileid"
    return "auto"


def resolve_pdf_transport_targets(*, purpose: str = "scoring") -> list[PdfTransportTarget]:
    """解析可承接 PDF 直传的通道目标列表(池序优先, DashScope 派生目标殿后)。

 - 默认消费 scoring 评分提取专用池(用户裁决 2026-08-15); 专用池未配置
 任何模型时回退 review 池, 保持既有行为零回退;
 - 目标顺序遵循调度池优先级(pool_priority 升序, 同级按池序,);
 - 池内配置: 能力档案已声明方言者直接纳入; auto 时按已知厂商主机推断
 (moonshot/stepfun=extract_text, 方舟=ark_file_url, dashscope文档模型=fileid),
 未知厂商不猜、显式 none 排除;
 - 派生目标: 池内存在 DashScope 凭据时, 追加"同凭据 + 文档模型覆写"的
 fileid 目标, 与已有目标按 (base,model,dialect) 去重;
 - 会话级 unsupported 标记的目标被过滤。
 """
    from app.core.ai_settings import load_ai_settings
    from app.core.scoring_pdf_rescue_config import scoring_pdf_rescue_settings as cfg

    settings_model = load_ai_settings()
    configs = settings_model.get_configs(purpose)
    if not configs and purpose == "scoring":
        purpose = "review"
        configs = settings_model.get_configs(purpose)
    by_id = {config.id: config for config in configs}
    pool_ids = [cid for cid in settings_model.get_pool_ids(purpose) if cid in by_id]
    ordered = [by_id[cid] for cid in pool_ids] or list(configs)
    # 用户在设置页配置的调度优先级是权威顺序(0=默认首选先行); 同级保持池序
    priority_of = getattr(settings_model, "get_priority_for", lambda p, cid: 100)
    ordered = [
        config for _, _, config in sorted(
            (priority_of(purpose, config.id), index, config)
            for index, config in enumerate(ordered)
        )
    ]

    targets: list[PdfTransportTarget] = []
    seen: set[tuple[str, str, str]] = set()
    for config in ordered:
        dialect = _resolve_dialect(config)
        key = _first_key(config)
        if dialect in ("auto", "none") or not key:
            continue
        dedupe = (config.api_base, config.model_name, dialect)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        targets.append(_make_target(
            config, dialect=dialect, timeout=cfg.SCORING_PDF_RESCUE_TIMEOUT_S,
        ))

    # DashScope 派生目标: 同凭据换文档模型(qwen-long/qwen-doc-turbo)
    for config in ordered:
        if "dashscope" not in _host(config.api_base):
            continue
        key = _first_key(config)
        if not key:
            continue
        doc_model = cfg.SCORING_PDF_RESCUE_DASHSCOPE_MODEL
        dedupe = (config.api_base, doc_model, "dashscope_fileid")
        if dedupe in seen:
            break
        seen.add(dedupe)
        targets.append(_make_target(
            config,
            dialect="dashscope_fileid",
            timeout=cfg.SCORING_PDF_RESCUE_TIMEOUT_S,
            model_name=doc_model,
            config_id=f"{config.id}#dashscope_doc",
        ))
        break

    return [t for t in targets if t.session_key not in _session_unsupported]


def _file_content_part(dialect: PdfDialect, pdf_base64: str, filename: str) -> dict:
    """按方言拼消息内文件内容块。"""
    data_url = f"data:application/pdf;base64,{pdf_base64}"
    if dialect == "openai_file":
        return {"type": "file", "file": {"filename": filename, "file_data": data_url}}
    if dialect == "ark_file_url":
        return {"type": "file_url", "file_url": {"url": data_url, "name": filename}}
    raise ValueError(f"非消息内文件块方言: {dialect}")


def _is_unsupported_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _UNSUPPORTED_STATUS


def _error_snippet(exc: Exception) -> str:
    """服务商报错体摘要(截断), 供 unsupported 判定留痕——禁止吞掉真因。"""
    parts = [
        str(getattr(exc, "message", "") or ""),
        str(getattr(exc, "body", "") or ""),
    ]
    return " ".join(part for part in parts if part)[:160]


def _is_temperature_rejection(exc: Exception) -> bool:
    """识别"该模型拒绝temperature参数"类 400(如 kimi-k3 only 1 is allowed)。"""
    if getattr(exc, "status_code", None) != 400:
        return False
    return "temperature" in _error_snippet(exc).lower()


def _record_trace(
    *, node_key: str, project_id: Optional[int], started: float,
    model_name: str, usage: object = None, error: bool = False,
) -> None:
    """经 PromptRegistry 记 trace(billing 同源), fire-and-forget 且失败不阻断。"""
    if not node_key:
        return
    try:
        from app.core.ai.prompt_registry import get_registry

        get_registry().record_call(
            node_key=node_key,
            project_id=project_id,
            latency_ms=round((time.monotonic() - started) * 1000, 1),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            error=error,
            model_name=model_name,
        )
    except Exception:  # noqa: BLE001 - trace 记录失败不阻断业务
        logger.debug("pdf_transport: trace 记录失败 node={}", node_key)


async def chat_with_pdf(
    target: PdfTransportTarget,
    *,
    pdf_bytes: bytes,
    pdf_base64: str,
    filename: str,
    system_message: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    node_key: str = "",
    project_id: Optional[int] = None,
) -> str:
    """按目标方言完成一次 PDF 附件对话, 返回模型文本。

    不支持文件输入的目标抛 PdfInputUnsupported(并做会话级标记);
    空回复抛 EmptyPdfCompletion(不拉黑, 由调用方换目标);
    其余异常原样上抛, 由调用方决定换通道或放弃。
    """
    from app.core.ai.prompt_registry import current_node_key

    started = time.monotonic()
    node_token = current_node_key.set(node_key) if node_key else None
    requested_temp = temperature
    if target.temperature_settings is not None:
        temperature = resolve_request_temperature(
            requested_temp, target.temperature_settings,
        )
        if temperature != requested_temp:
            logger.info(
                "[pdf_transport] {} 温度已按模型能力归一化: requested={} resolved={} mode={}",
                target.label, requested_temp, temperature,
                target.temperature_settings.mode,
            )

    client = AsyncOpenAI(
        base_url=target.api_base,
        api_key=target.api_key,
        timeout=target.timeout,
        max_retries=0,
    )

    async def _dispatch(temp: Optional[float]) -> tuple[str, object]:
        if target.dialect in ("openai_file", "ark_file_url"):
            return await _chat_file_part(
                client, target,
                pdf_base64=pdf_base64, filename=filename,
                system_message=system_message, user_message=user_message,
                temperature=temp, max_tokens=max_tokens,
            )
        if target.dialect == "dashscope_fileid":
            return await _chat_dashscope_fileid(
                client, target,
                pdf_bytes=pdf_bytes, filename=filename,
                system_message=system_message, user_message=user_message,
                temperature=temp, max_tokens=max_tokens,
            )
        if target.dialect == "extract_text":
            return await _chat_extract_text(
                client, target,
                pdf_bytes=pdf_bytes, filename=filename,
                system_message=system_message, user_message=user_message,
                temperature=temp, max_tokens=max_tokens,
            )
        raise ValueError(f"未知PDF方言: {target.dialect}")  # pragma: no cover

    # 已知拒绝temperature的目标直接省参, 避免每次白付一次400
    effective_temp: Optional[float] = (
        None if target.session_key in _session_no_temperature else temperature
    )
    try:
        try:
            text, usage = await _dispatch(effective_temp)
        except APIStatusError as exc:
            if effective_temp is None or not _is_temperature_rejection(exc):
                raise
            # kimi-k3 类推理模型仅接受服务端默认温度, 去参重试并会话记忆
            _session_no_temperature.add(target.session_key)
            logger.info(
                "[pdf_transport] {} 拒绝temperature参数, 改用服务端默认重试: {}",
                target.label, _error_snippet(exc)[:80],
            )
            text, usage = await _dispatch(None)
    except APIStatusError as exc:
        if _is_unsupported_error(exc):
            mark_target_unsupported(target)
            _record_trace(
                node_key=node_key, project_id=project_id, started=started,
                model_name=target.model_name, error=True,
            )
            _record_pdf_attempt_error(target, exc, started)
            raise PdfInputUnsupported(
                f"{target.label}({target.dialect}): "
                f"HTTP {exc.status_code} {_error_snippet(exc)}"
            ) from exc
        _record_trace(
            node_key=node_key, project_id=project_id, started=started,
            model_name=target.model_name, error=True,
        )
        _record_pdf_attempt_error(target, exc, started)
        raise
    except Exception as exc:
        _record_trace(
            node_key=node_key, project_id=project_id, started=started,
            model_name=target.model_name, error=True,
        )
        _record_pdf_attempt_error(target, exc, started)
        raise
    else:
        _record_trace(
            node_key=node_key, project_id=project_id, started=started,
            model_name=target.model_name, usage=usage,
        )
        _record_pdf_attempt_success(target, text, usage, started)
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响结果
            pass
        if node_token is not None:
            current_node_key.reset(node_token)

    if not str(text or "").strip():
        raise EmptyPdfCompletion(f"{target.label}: empty pdf completion")
    return text


def _thinking_extra(target: PdfTransportTarget) -> dict:
    """按用户能力档案注入思考参数; 无档案或厂商不支持时不下发。"""
    profile = target.capability_profile
    if profile is None or not hasattr(profile, "thinking_policy"):
        return {}
    from app.core.ai.dispatcher import detect_provider
    from app.core.ai.thinking_params import build_thinking_extra_body

    body = build_thinking_extra_body(detect_provider(target.api_base), profile)
    return {"extra_body": body} if body else {}


def _chat_params(
    target: PdfTransportTarget, messages: list, temperature: Optional[float],
    max_tokens: int,
) -> dict:
    """组装 chat.completions.create 参数; temperature=None 时省参用服务端默认。"""
    params: dict = {
        "model": target.model_name,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        params["temperature"] = temperature
    params.update(_thinking_extra(target))
    return params


async def _chat_file_part(
    client: AsyncOpenAI, target: PdfTransportTarget, *,
    pdf_base64: str, filename: str, system_message: str, user_message: str,
    temperature: Optional[float], max_tokens: int,
) -> tuple[str, object]:
    """机制A: 消息内文件内容块(openai_file / ark_file_url), 单次无状态调用。"""
    completion = await client.chat.completions.create(**_chat_params(
        target,
        [
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": [
                    _file_content_part(target.dialect, pdf_base64, filename),
                    {"type": "text", "text": user_message},
                ],
            },
        ],
        temperature, max_tokens,
    ))
    return _completion_text(completion), getattr(completion, "usage", None)


async def _chat_dashscope_fileid(
    client: AsyncOpenAI, target: PdfTransportTarget, *,
    pdf_bytes: bytes, filename: str, system_message: str, user_message: str,
    temperature: Optional[float], max_tokens: int,
) -> tuple[str, object]:
    """机制B-DashScope: Files API 上传 -> 第二条 system 消息 fileid:// 引用。

    注意: fileid 形态下 user 消息限 9000 token(官方约束); 调用后即删文件,
    释放账户配额(1万个/100GB 上限)。
    """
    file_object = await client.files.create(
        file=(filename, pdf_bytes, "application/pdf"), purpose="file-extract"
    )
    try:
        completion = await client.chat.completions.create(**_chat_params(
            target,
            [
                {"role": "system", "content": system_message},
                {"role": "system", "content": f"fileid://{file_object.id}"},
                {"role": "user", "content": user_message},
            ],
            temperature, max_tokens,
        ))
    finally:
        await _delete_file_quiet(client, file_object.id, target.label)
    return _completion_text(completion), getattr(completion, "usage", None)


async def _chat_extract_text(
    client: AsyncOpenAI, target: PdfTransportTarget, *,
    pdf_bytes: bytes, filename: str, system_message: str, user_message: str,
    temperature: Optional[float], max_tokens: int,
) -> tuple[str, object]:
    """机制B-extract-text(Kimi/阶跃式): 上传解析 -> 取回文本 -> 拼入消息。"""
    from app.core.scoring_pdf_rescue_config import scoring_pdf_rescue_settings as cfg

    file_object = await client.files.create(
        file=(filename, pdf_bytes, "application/pdf"), purpose="file-extract"
    )
    try:
        content = await client.files.content(file_object.id)
        doc_text = str(getattr(content, "text", "") or "")
        limit = cfg.SCORING_PDF_RESCUE_EXTRACT_TEXT_MAX_CHARS
        if len(doc_text) > limit:
            doc_text = doc_text[:limit]
        completion = await client.chat.completions.create(**_chat_params(
            target,
            [
                {"role": "system", "content": system_message},
                {"role": "system", "content": doc_text},
                {"role": "user", "content": user_message},
            ],
            temperature, max_tokens,
        ))
    finally:
        await _delete_file_quiet(client, file_object.id, target.label)
    return _completion_text(completion), getattr(completion, "usage", None)


async def _delete_file_quiet(client: AsyncOpenAI, file_id: str, label: str) -> None:
    """删除服务商侧文件(配额释放); 失败仅告警不阻断。"""
    try:
        await client.files.delete(file_id)
    except Exception as exc:  # noqa: BLE001 - 删除失败不影响调用结果
        logger.warning("pdf_transport: 文件删除失败 {} file_id={}: {}", label, file_id, exc)


def _completion_text(completion) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = str(getattr(message, "content", "") or "").strip()
    if content:
        return content
    for attr in ("reasoning_content", "reasoning"):
        extra = getattr(message, attr, None)
        if extra and str(extra).strip():
            return str(extra).strip()
    return ""


def _synthetic_completion(text: str, usage: object) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text or ""))],
        usage=usage,
    )


def _record_pdf_attempt_success(
    target: PdfTransportTarget, text: str, usage: object, started: float
) -> None:
    try:
        from app.core.ai.attempt_recorder import get_attempt_recorder

        get_attempt_recorder().record_success(
            _synthetic_completion(text, usage),
            latency_ms=int((time.monotonic() - started) * 1000),
            model_name=target.model_name,
            method="chat_with_pdf",
        )
    except Exception:  # noqa: BLE001 - 观测不得阻断传输
        logger.debug("pdf_transport: attempt success 记录失败 label={}", target.label)


def _record_pdf_attempt_error(
    target: PdfTransportTarget, exc: BaseException, started: float
) -> None:
    try:
        from app.core.ai.attempt_recorder import get_attempt_recorder

        get_attempt_recorder().record_error(
            exc,
            latency_ms=int((time.monotonic() - started) * 1000),
            model_name=target.model_name,
            method="chat_with_pdf",
        )
    except Exception:  # noqa: BLE001 - 观测不得阻断传输
        logger.debug("pdf_transport: attempt error 记录失败 label={}", target.label)


__all__ = [
    "EmptyPdfCompletion",
    "PdfDialect",
    "PdfInputUnsupported",
    "PdfTransportTarget",
    "chat_with_pdf",
    "mark_target_unsupported",
    "resolve_pdf_transport_targets",
]
