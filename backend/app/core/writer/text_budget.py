"""
职责: 提供LLM输入文本预算收缩的共享纯函数与收缩器工厂
不做: 不做prompt构造、不做payload字段决策、不访问DB与网络
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

PayloadDict = dict[str, object]
ReduceResult = tuple[PayloadDict, dict[str, object]]


def shrink_semantic_text(text: str, *, min_lines: int = 1) -> str:
    """按行收缩条目式上下文，保留完整语义条。

    原为 outline_generator / clause_extractor / section_composer /
    outline_scope.extractor / industry_profiler 五处逐字相同的本地定义，
    L-7 合并至此；各模块以 ``as _shrink_semantic_text`` 别名保留原符号。
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= min_lines:
        return text
    keep_count = max(min_lines, len(lines) - max(1, len(lines) // 3))
    return "\n".join(lines[:keep_count])


def make_priority_shrink_reducer(
    priority_fields: Sequence[tuple[str, int]],
    kept_segments: Sequence[str],
    *,
    coerce_str: bool = False,
    pre_branch: Optional[Callable[[PayloadDict], Optional[str]]] = None,
) -> Callable[[PayloadDict, int], ReduceResult]:
    """构造 input_guard 用的 ``reduce_payload_semantically`` 收缩器。

    与原七处手写闭包行为逐字节等价（对拍见 test_text_budget_parity.py）:

    - ``priority_fields`` 为 ``(字段名, min_lines)`` 优先级列表，按序找到
      第一个非空字段收缩即停; 末位字段为兜底分支，不判空直接收缩
      （对应原闭包的 ``else`` 分支）。
    - ``pre_branch`` 为可选前置分支（如 previous_summaries 列表收缩）:
      入参为可变的 reduced dict，命中时原地收缩并返回段名以短路字段链，
      未命中返回 None。
    - ``coerce_str`` 为真时收缩前做 ``str()`` 包装（composer 路由原有写法）。
    """
    *head_fields, (last_name, last_min_lines) = list(priority_fields)

    def _shrink_field(reduced: PayloadDict, name: str, min_lines: int) -> None:
        value = reduced[name]
        if coerce_str:
            value = str(value)
        reduced[name] = shrink_semantic_text(value, min_lines=min_lines)  # type: ignore[arg-type]

    def _reduce_by_priority(payload: PayloadDict, retry_round: int) -> ReduceResult:
        reduced = dict(payload)
        removed_segments: list[str] = []
        taken = pre_branch(reduced) if pre_branch is not None else None
        if taken is not None:
            removed_segments.append(taken)
        else:
            for name, min_lines in head_fields:
                if reduced.get(name):
                    _shrink_field(reduced, name, min_lines)
                    removed_segments.append(name)
                    break
            else:
                _shrink_field(reduced, last_name, last_min_lines)
                removed_segments.append(last_name)
        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": list(kept_segments),
            "retry_attempt": retry_round,
        }

    return _reduce_by_priority
