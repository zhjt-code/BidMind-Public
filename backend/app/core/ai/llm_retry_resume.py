"""批次外层重试可继承的 LLM 问题状态

职责: 承载内层校验重试耗尽后仍可交给批次外层重试的字段级提示、半成品输出与已用输出预算
不做: 不发起 LLM 调用、不判断是否恢复、不写 DB、不绑定具体业务实体词
"""

from __future__ import annotations

from dataclasses import dataclass

# 半成品回灌上限：防止把超长截断输出再次撑爆输入窗口
DEFAULT_RAW_CLIP = 2000


@dataclass
class LlmRetryResumeState:
    last_retry_hint: str = ""
    last_raw_output: str = ""
    last_max_tokens: int | None = None

    def has_payload(self) -> bool:
        return bool(self.last_retry_hint or self.last_raw_output or self.last_max_tokens)

    def capture(
        self,
        *,
        retry_hint: str = "",
        raw_output: str = "",
        max_tokens: int | None = None,
    ) -> None:
        """用最近一次失败覆盖可继承字段；空入参不擦除已有值。"""
        if retry_hint:
            self.last_retry_hint = retry_hint
        if raw_output:
            self.last_raw_output = raw_output
        if max_tokens is None:
            return
        try:
            value = int(max_tokens)
        except (TypeError, ValueError):
            return
        if value > 0:
            self.last_max_tokens = value

    def prompt_suffix(self, raw_clip: int = DEFAULT_RAW_CLIP) -> str:
        """拼给外层首轮的问题状态：缺字段提示 + 截断后的半成品。"""
        if not self.has_payload():
            return ""
        try:
            clip_at = int(raw_clip)
        except (TypeError, ValueError):
            clip_at = DEFAULT_RAW_CLIP
        if clip_at <= 0:
            clip_at = DEFAULT_RAW_CLIP

        parts: list[str] = [
            "\n\n[上次调用未通过校验。请在上次输出基础上补齐缺失字段，"
            "不要从零重写；长文本之前先写完短字段。]"
        ]
        hint = (self.last_retry_hint or "").strip()
        if hint:
            parts.append("\n")
            parts.append(hint)
        raw = self.last_raw_output or ""
        if raw:
            clip = raw if len(raw) <= clip_at else f"{raw[:clip_at]}\n…(已截断)"
            parts.append("\n\n[上次未通过校验的输出（可能不完整）:]\n")
            parts.append(clip)
        return "".join(parts)


def merge_resume_max_tokens(
    node_default: int,
    resume: LlmRetryResumeState | None,
    *,
    ceiling: int,
) -> int:
    """外层起步预算 = max(节点默认, 已用额度)，且不超过封顶。"""
    try:
        baseline = int(node_default)
    except (TypeError, ValueError):
        baseline = 1500
    if baseline <= 0:
        baseline = 1500
    try:
        cap = int(ceiling)
    except (TypeError, ValueError):
        cap = baseline
    if cap <= 0:
        cap = baseline
    if resume is None or resume.last_max_tokens is None:
        return min(baseline, cap)
    try:
        carried = int(resume.last_max_tokens)
    except (TypeError, ValueError):
        return min(baseline, cap)
    if carried <= 0:
        return min(baseline, cap)
    return min(max(baseline, carried), cap)
