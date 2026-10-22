"""评分裁决轨迹采集通道（治理阶段 3；独立于生产输出）。

职责: 在确定性主干的关键裁决分支上记录"走了哪一条"，供回归差分定位首个变化点。
不做: 不改变任何裁决结果、不写 diagnostics、不落库、不参与 needs_review 判定。

为什么必须是独立通道（不得复用 diagnostics）:
  `parsed.diagnostics` 会经 `scoring_extractor` → `_save_items` → `requirement_service`
  → `score_totals._merge_extra_diagnostics` 进入用户可见报告，并且
  `scoring_diagnostic_policy.diagnostics_require_review` 会据其内容翻转 needs_review。
  往里塞定位信息等于改变生产落库与裁决结果，直接违反零倒退约束。

开销约束:
  默认关闭。关闭态下 `record()` 首行即返回（一次 ContextVar 读取），
  生产路径不构造任何对象、不拼接字符串。

用法:
    with capture_decision_trace() as trace:
        ...  # 运行提取链
    trace.hits          # {branch_id: 命中次数}
    trace.ordered_ids   # 按首次命中顺序排列的 branch_id

branch_id 命名规则: `<域>/<裁决语义>`，锚定**语义**而非代码位置，
使其在后续文件拆分中随语义搬家而不失效。全部 ID 登记于 BRANCH_REGISTRY.md，
由 test_scoring_decision_trace.py 机械校验登记完整性。
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

__all__ = [
    "DecisionTrace",
    "capture_decision_trace",
    "is_tracing",
    "record",
]


@dataclass
class DecisionTrace:
    """单次采集会话的裁决命中记录。"""

    hits: dict[str, int] = field(default_factory=dict)
    ordered_ids: list[str] = field(default_factory=list)

    def add(self, branch_id: str) -> None:
        if branch_id not in self.hits:
            self.hits[branch_id] = 0
            self.ordered_ids.append(branch_id)
        self.hits[branch_id] += 1

    def as_snapshot(self) -> dict[str, int]:
        """稳定序列化：按 branch_id 字典序，供快照差分。"""
        return {key: self.hits[key] for key in sorted(self.hits)}


_SINK: ContextVar[DecisionTrace | None] = ContextVar(
    "scoring_decision_trace", default=None
)


def record(branch_id: str) -> None:
    """记录一次裁决分支命中；未开启采集时零开销返回。"""
    sink = _SINK.get()
    if sink is None:
        return
    sink.add(branch_id)


def is_tracing() -> bool:
    """当前上下文是否处于采集态（供测试断言开关生效）。"""
    return _SINK.get() is not None


@contextlib.contextmanager
def capture_decision_trace() -> Iterator[DecisionTrace]:
    """开启一次采集会话；退出后自动恢复原状态（可嵌套）。"""
    trace = DecisionTrace()
    token = _SINK.set(trace)
    try:
        yield trace
    finally:
        _SINK.reset(token)
