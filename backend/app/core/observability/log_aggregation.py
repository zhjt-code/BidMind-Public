"""错误日志同源聚合 -- 把重复的同指纹日志折叠成一行并累加发生次数。

职责: 维护"聚合键 -> 已入库锚点行"的进程内窗口映射, 判定新日志该新增行还是累加计数
不做: 不落库、不感知 loguru record 结构(只接受已构建好的 entry 字典)、不做查询侧过滤

背景: 原先 WARNING 在 5 分钟窗口内重复直接丢弃, "这个问题发生了多少次"
被永久丢失; ERROR 则每次回声新增一行, 单个未修复缺陷能刷出上千行。两者都让日志
条数失去判断价值。改为窗口内首条落库为锚点、后续回声只累加 occurrence_count。

窗口语义(固定窗口, 非滑动): 锚点在建立时定下 opened_at, 回声不会为其续期。
持续回声会每隔一个窗口重新开一行, 例如 1Hz 持续 30 分钟 + 300s 窗口 = 6 行
(每行 occurrence_count=300, 合计 1800 次, 一次不丢)。这是刻意选择: 若窗口随回声
滚动续期, 长期不修的问题会永远压在同一行里, 丢失"什么时候在发生"的时间分辨率。

已知取舍:
- 锚点缓存是进程内的, API 与 Worker 各自持有一份, 同一问题最多各留一行(而非上千行)
- 进程重启后锚点丢失, 窗口内会多出一行, 属可接受误差
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

_DEFAULT_WINDOW_SECONDS = 300.0
# 锚点缓存上限: 防止指纹爆炸时无界增长, 超限按最旧优先淘汰
_DEFAULT_MAX_KEYS = 5000


@dataclass
class _Anchor:
    """窗口内某个聚合键对应的锚点行"""

    opened_at: float
    row_id: Optional[int] = None


def build_aggregation_key(entry: dict) -> Optional[str]:
    """由日志 entry 计算聚合键; 返回 None 表示该条不参与聚合, 按原样入库。

    分级策略:
    - WARNING 属高频低价值噪声, 跨 incident 聚合, 最大化压缩
    - ERROR / CRITICAL 把 incident_id 纳入键, 只折叠同一次失败链路内的回声,
      不同 incident 各自留行, 保住 incident_id / chain_seq / is_root_cause 的可追溯性
    """
    fingerprint = entry.get("message_fingerprint")
    if not fingerprint:
        return None
    incident = "" if entry.get("level") == "WARNING" else (entry.get("incident_id") or "")
    return "|".join((
        str(fingerprint),
        str(entry.get("traffic_class") or ""),
        str(entry.get("project_id")),
        incident,
    ))


class LogAggregator:
    """线程安全的日志聚合窗口"""

    def __init__(
        self,
        *,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        max_keys: int = _DEFAULT_MAX_KEYS,
    ) -> None:
        self._window = window_seconds
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._anchors: dict[str, _Anchor] = {}
        self._pending: dict[str, int] = {}

    def admit(self, entry: dict) -> tuple[bool, Optional[str]]:
        """判定 entry 是否需要新增一行。

        Returns:
            (should_insert, agg_key)。should_insert=False 时该条已被记为回声,
            调用方必须丢弃 entry(计数已累积到 _pending, 由 flush 落库)。
        """
        key = build_aggregation_key(entry)
        if key is None:
            return True, None

        now = time.monotonic()
        with self._lock:
            anchor = self._anchors.get(key)
            if anchor is not None and (now - anchor.opened_at) < self._window:
                self._pending[key] = self._pending.get(key, 0) + 1
                return False, key
            self._anchors[key] = _Anchor(opened_at=now)
            self._evict_locked(now)
            return True, key

    def bind_anchor(self, agg_key: str, row_id: int) -> None:
        """锚点行提交成功后回填自增 id, 供后续回声定位更新目标"""
        with self._lock:
            anchor = self._anchors.get(agg_key)
            if anchor is not None and anchor.row_id is None:
                anchor.row_id = row_id

    def anchor_id(self, agg_key: str) -> Optional[int]:
        with self._lock:
            anchor = self._anchors.get(agg_key)
            return anchor.row_id if anchor is not None else None

    def take_pending(self) -> dict[str, int]:
        """取走并清空待累加计数"""
        with self._lock:
            pending = self._pending
            self._pending = {}
            return pending

    def restore_pending(self, pending: dict[str, int]) -> None:
        """写库失败或锚点尚未落库时把计数放回, 避免发生次数被静默吞掉"""
        if not pending:
            return
        with self._lock:
            for key, delta in pending.items():
                self._pending[key] = self._pending.get(key, 0) + delta

    def discard_anchors(self, agg_keys: list[str]) -> None:
        """写库失败时丢弃未落库的锚点, 让下一条同源日志重新建行"""
        if not agg_keys:
            return
        with self._lock:
            for key in agg_keys:
                self._anchors.pop(key, None)

    def _evict_locked(self, now: float) -> None:
        """清理过期锚点; 仍超上限时按最旧优先淘汰(在 _lock 内调用)"""
        expired = [k for k, a in self._anchors.items() if now - a.opened_at > self._window]
        for key in expired:
            del self._anchors[key]
        overflow = len(self._anchors) - self._max_keys
        if overflow <= 0:
            return
        oldest = sorted(self._anchors.items(), key=lambda kv: kv[1].opened_at)[:overflow]
        for key, _ in oldest:
            del self._anchors[key]
