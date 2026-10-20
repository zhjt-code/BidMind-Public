"""分块预算组装器：在字符预算内语义安全地组装 Prompt 上下文。

职责: 给定一组带优先级的语义块与字符预算，按"骨架永不裁、按优先级整块取舍、
 单块超限首尾保留"的策略组装文本，并返回可观测 meta 与 fail-loud 信号。
不做: 不感知业务语义(不知道谁是招标要求/证据)，不做 token 精确计数，
 不发起 LLM 调用，不决定预算数值策略(首次不裁/400 后递减由调用方用
 input_guard 驱动)。

设计动机: 输入超长 400 的收缩必须语义正确，禁止在通用层按字符腰斩
中段。本模块把"如何在预算内安全取舍语义块"沉淀为可复用纯函数，供
reviewer/critic/extractor 各链路共用，避免逐点重复实现导致裁剪质量参差。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

_DEFAULT_SEPARATOR = "\n"
_DEFAULT_TRUNCATION_MARKER = "\n...[TRUNCATED]...\n"
# 截断后保留的最小有意义长度：低于此宁可整块丢弃，避免留下无法理解的碎片
_MIN_MEANINGFUL_KEEP_CHARS = 120


def clip_text_keep_ends(
    text: str,
    limit: int,
    marker: str = _DEFAULT_TRUNCATION_MARKER,
) -> str:
    """首尾保留式截断：保留片段首尾关键信息，中段以标记替代，避免语义被腰斩。

 复用 bid_extractor._clip_context_text已验证的首尾保留思路，
 并补齐 limit<=0 的边界处理。
 """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(marker) + 16:
        # 预算过小无法容纳标记，退化为纯首部截断
        return text[:limit]
    head = max(24, int(limit * 0.7))
    tail = max(12, limit - head - len(marker))
    if head + tail + len(marker) > limit:
        head = max(12, limit - tail - len(marker))
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


@dataclass(frozen=True)
class PromptBlock:
    """一个可独立取舍的语义块。

    - priority: 越小越优先保留；预算紧张时先牺牲 priority 大的块。
    - is_skeleton: 骨架块永不丢弃、永不截断，是语义正确性的底线
      (系统提示/招标要求/投标响应主体/输出 schema 等)。
    - clippable: False 时该块只能整块保留或整块丢弃，不允许截断。
    - min_keep_chars: 允许截断保留的最小字符数，低于此则整块丢弃。
    """

    text: str
    priority: int = 100
    is_skeleton: bool = False
    label: str = ""
    clippable: bool = True
    min_keep_chars: int = _MIN_MEANINGFUL_KEEP_CHARS


@dataclass(frozen=True)
class BudgetedAssembly:
    """组装结果与可观测 meta。

    over_budget=True 是 fail-loud 信号：骨架本身已超出预算，调用方据此应停止
    重试并显式报错/降级，禁止继续发送被破坏的 prompt。
    """

    text: str
    used_chars: int
    budget_chars: int
    skeleton_chars: int
    over_budget: bool
    kept_labels: tuple[str, ...] = ()
    truncated_labels: tuple[str, ...] = ()
    dropped_labels: tuple[str, ...] = ()

    def as_log_meta(self) -> dict[str, object]:
        """供 input_guard.describe_payload 使用的精简观测信息。"""
        return {
            "budget_chars": self.budget_chars,
            "used_chars": self.used_chars,
            "skeleton_chars": self.skeleton_chars,
            "over_budget": self.over_budget,
            "kept": list(self.kept_labels),
            "truncated": list(self.truncated_labels),
            "dropped": list(self.dropped_labels),
        }


def assemble_within_budget(
    blocks: Sequence[PromptBlock],
    budget_chars: int,
    *,
    separator: str = _DEFAULT_SEPARATOR,
    clip_fn: Callable[[str, int], str] = clip_text_keep_ends,
) -> BudgetedAssembly:
    """在 budget_chars 内按语义安全策略组装 blocks。

    策略:
    1. 骨架块(is_skeleton)恒保留、不截断；其拼接长度超预算即 over_budget=True。
    2. 可选块按 (priority, 原始顺序) 决定保留次序：整块放得下→保留；放不下但
       clippable 且剩余预算≥min_keep_chars→首尾保留式截断；否则整块丢弃。
    3. 最终按 blocks 的原始顺序拼接，保持调用方期望的布局。

    返回的 BudgetedAssembly.over_budget 为 fail-loud 信号，由调用方处置。
    """
    indexed = [
        (i, b) for i, b in enumerate(blocks) if b.text and b.text.strip()
    ]
    sep_len = len(separator)

    kept: dict[int, str] = {}
    kept_labels: list[str] = []
    truncated_labels: list[str] = []
    dropped_labels: list[str] = []

    # 1. 骨架块恒保留(永不截断)
    skeleton_items = [(i, b) for i, b in indexed if b.is_skeleton]
    for i, b in skeleton_items:
        kept[i] = b.text
        kept_labels.append(b.label)
    skeleton_chars = (
        len(separator.join(b.text for _, b in skeleton_items))
        if skeleton_items
        else 0
    )

    over_budget = budget_chars < 0 or skeleton_chars > budget_chars
    remaining = max(budget_chars - skeleton_chars, 0)

    # 2. 可选块按 (优先级, 原始顺序) 取舍
    optional_items = sorted(
        (item for item in indexed if not item[1].is_skeleton),
        key=lambda item: (item[1].priority, item[0]),
    )
    for i, b in optional_items:
        if over_budget:
            dropped_labels.append(b.label)
            continue
        need = len(b.text) + sep_len
        if need <= remaining:
            kept[i] = b.text
            kept_labels.append(b.label)
            remaining -= need
        elif b.clippable and (remaining - sep_len) >= b.min_keep_chars:
            clipped = clip_fn(b.text, remaining - sep_len)
            if clipped:
                kept[i] = clipped
                truncated_labels.append(b.label)
                remaining -= len(clipped) + sep_len
            else:
                dropped_labels.append(b.label)
        else:
            dropped_labels.append(b.label)

    # 3. 按原始顺序拼接，保持调用方布局
    ordered_text = separator.join(kept[i] for i, _ in indexed if i in kept)
    return BudgetedAssembly(
        text=ordered_text,
        used_chars=len(ordered_text),
        budget_chars=budget_chars,
        skeleton_chars=skeleton_chars,
        over_budget=over_budget,
        kept_labels=tuple(kept_labels),
        truncated_labels=tuple(truncated_labels),
        dropped_labels=tuple(dropped_labels),
    )


# --------------------------------------------------------------------------
# 发送前硬上限预检（首发于审查侧, 下沉至此供提取链共用）
# --------------------------------------------------------------------------

# 中文场景 token 估算口径: 与 VectorIndexer._estimate_tokens 同源(1 token ≈ 2 字符)
_CHARS_PER_TOKEN = 2


def estimate_prompt_tokens(text: str) -> int:
    """中文场景 token 估算(1 token ≈ 2 字符), 与向量层同口径。"""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def prompt_hard_cap_violation(
    system_message: str,
    user_prompt: str,
) -> str | None:
    """发送前硬上限预检: 字符 / 估算 token / UTF-8 字节三项。

 返回超限原因文案(用户可读, 不含内部字段名); 未超限返回 None。
 禁止先发送超限请求再等待 provider 400 或超时（既有失效路径）。
 上限取模型级配置 REVIEW_PROMPT_HARD_CAP_TOKENS / _BYTES（描述云端模型
 输入限制, 与业务流无关, 审查/提取链共用同一组值）。
 """
    from app.core.config import settings

    combined = f"{system_message or ''}\n{user_prompt or ''}"

    max_tokens = int(getattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 0) or 0)
    if max_tokens > 0:
        max_chars = max_tokens * _CHARS_PER_TOKEN
        if len(combined) > max_chars:
            return f"输入约 {len(combined)} 字符，超过上限 {max_chars} 字符"
        tokens = estimate_prompt_tokens(combined)
        if tokens > max_tokens:
            return f"输入估算 {tokens} tokens，超过上限 {max_tokens} tokens"

    max_bytes = int(getattr(settings, "REVIEW_PROMPT_HARD_CAP_BYTES", 0) or 0)
    if max_bytes > 0:
        byte_len = len(combined.encode("utf-8", errors="ignore"))
        if byte_len > max_bytes:
            return f"请求体 {byte_len} 字节，超过上限 {max_bytes} 字节"

    return None
