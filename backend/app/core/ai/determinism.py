"""S.3.1 · LLM 确定性辅助：seed 派生与取值。

核心入口：``derive_seed(project_id, node_key)`` 返回稳定 seed。

设计原则：
- 同一 (project_id, node_key) 组合得到的 seed 值跨进程、跨部署一致
- 任一输入为 None / 空字符串时返回 None（provider 会用自身默认随机源）
- seed 范围 [0, 2**31 - 1]，兼容 OpenAI / DeepSeek / Qwen 等主流 provider
- 纯函数、无 I/O、无副作用，可直接缓存

备注：此模块只负责"把业务语义转成稳定整数"，是否真正透传到 API 由
``CloudClient._resolve_seed`` 与 ``settings.LLM_SEED_ENABLED`` 决定。
"""

from __future__ import annotations

import contextvars
import hashlib
from typing import Optional


_SEED_UPPER_BOUND = 2**31 - 1  # int32 max, 符合 OpenAI/DeepSeek/Qwen 约定

# S.3.1: 由 ProjectBoundDispatcher 在每次 dispatch 入口 set；CloudClient._resolve_seed 读取
current_project_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "current_project_id", default=None,
)


def derive_seed(
    project_id: int | str | None,
    node_key: str | None = None,
) -> Optional[int]:
    """按 ``(project_id, node_key)`` 派生稳定 seed。

    Args:
        project_id: 项目 ID。None 或空串时仅用 node_key 派生
        node_key: 当前 PromptRegistry 节点 key。None 或空串时仅用 project_id 派生

    Returns:
        int: 0 ~ 2**31-1 的稳定 seed；两个输入都不可用时返回 None

    两个输入都不可用 → 无法派生 → 返回 None。
    """
    pid_str = ""
    nk_str = ""
    if project_id is not None:
        pid_str = str(project_id).strip()
    if node_key is not None:
        nk_str = str(node_key).strip()

    if not pid_str and not nk_str:
        return None

    if pid_str and nk_str:
        composite = f"{pid_str}::{nk_str}"
    elif pid_str:
        composite = pid_str
    else:
        composite = nk_str

    digest = hashlib.sha256(composite.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return raw % (_SEED_UPPER_BOUND + 1)


def resolve_current_seed(
    project_id: int | str | None = None,
    explicit_seed: Optional[int] = None,
) -> Optional[int]:
    """按调用链上下文解析当前应使用的 seed。

    优先级：``explicit_seed`` > ``derive_seed(project_id, current_node_key)`` > None。
    当 ``settings.LLM_SEED_ENABLED`` 为 False 时直接返回 None 不派生。

    从 ``prompt_registry.current_node_key`` contextvar 读取当前节点 key（无侵入）。
    """
    from app.core.config import settings

    if not getattr(settings, "LLM_SEED_ENABLED", True):
        return None

    if explicit_seed is not None:
        return int(explicit_seed) % (_SEED_UPPER_BOUND + 1)

    try:
        from app.core.ai.prompt_registry import current_node_key as _nk_var
        node_key = _nk_var.get()
    except Exception:
        node_key = None

    effective_pid = project_id
    if effective_pid is None:
        try:
            effective_pid = current_project_id.get()
        except Exception:
            effective_pid = None

    return derive_seed(effective_pid, node_key)
