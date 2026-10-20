"""
职责: 版面实例的选择、失败计数、冷却与恢复
不做: 不渲染页面、不发推理请求、不解析版面块
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from loguru import logger

_Clock = Callable[[], float]
_Probe = Callable[[str], bool]


class StructurePool:
    """在健康实例中选在飞请求最少的一台, 连续失败后冷却, 探测通过后再接单。"""

    def __init__(
        self,
        urls: list[str],
        *,
        fail_threshold: int = 2,
        cooldown_sec: float = 60.0,
        clock: Optional[_Clock] = None,
        probe: Optional[_Probe] = None,
    ) -> None:
        self._urls = list(urls)
        self._fail_threshold = fail_threshold
        self._cooldown_sec = cooldown_sec
        self._clock = clock or time.monotonic
        self._probe = probe
        self._lock = threading.Lock()
        self._inflight = {url: 0 for url in self._urls}
        self._consecutive = {url: 0 for url in self._urls}
        self._cooled_until = {url: 0.0 for url in self._urls}
        self._cursor = 0

    def acquire_instance(self, exclude: Optional[set[str]] = None) -> Optional[str]:
        """占用一台可用实例。没有可用实例时返回 None。"""
        skipped = exclude or set()
        self._recover_expired()
        with self._lock:
            now = self._clock()
            available = [
                url for url in self._urls
                if url not in skipped and self._cooled_until[url] <= now
            ]
            if not available:
                return None
            least = min(self._inflight[url] for url in available)
            chosen = self._pick(available, least)
            self._inflight[chosen] += 1
            return chosen

    def report_result(self, url: str, ok: bool) -> None:
        """归还在飞计数。成功清零连续失败; 连续失败达到阈值则冷却。"""
        with self._lock:
            self._inflight[url] = max(0, self._inflight[url] - 1)
            if ok:
                self._consecutive[url] = 0
                return
            self._consecutive[url] += 1
            if self._consecutive[url] < self._fail_threshold:
                return
            self._cooled_until[url] = self._clock() + self._cooldown_sec
        logger.warning(f"[文档质检] 版面实例冷却: url={url}, seconds={self._cooldown_sec}")

    def _pick(self, available: list[str], least: int) -> str:
        count = len(self._urls)
        for _ in range(count):
            url = self._urls[self._cursor % count]
            self._cursor += 1
            if url in available and self._inflight[url] == least:
                return url
        return available[0]

    def _recover_expired(self) -> None:
        now = self._clock()
        with self._lock:
            due = [url for url in self._urls if 0 < self._cooled_until[url] <= now]
        for url in due:
            healthy = True if self._probe is None else bool(self._probe(url))
            with self._lock:
                if self._cooled_until[url] > self._clock():
                    continue
                if healthy:
                    self._cooled_until[url] = 0.0
                    self._consecutive[url] = 0
                    logger.info(f"[文档质检] 版面实例恢复: url={url}")
                    continue
                self._cooled_until[url] = self._clock() + self._cooldown_sec


_shared: Optional[StructurePool] = None
_shared_key: Optional[tuple[str, ...]] = None
_shared_lock = threading.Lock()


def get_structure_pool() -> StructurePool:
    """进程内共享实例池。地址列表变化时重建。"""
    global _shared, _shared_key
    from app.core.config import settings

    raw = str(getattr(settings, "DOCUMENT_QA_STRUCTURE_URLS", "") or "")
    urls = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    key = tuple(urls)
    with _shared_lock:
        if _shared is None or _shared_key != key:
            _shared = StructurePool(
                urls,
                fail_threshold=int(getattr(settings, "DOCUMENT_QA_STRUCTURE_FAIL_THRESHOLD", 2)),
                cooldown_sec=float(getattr(settings, "DOCUMENT_QA_STRUCTURE_COOLDOWN_SEC", 60.0)),
                probe=_probe_health,
            )
            _shared_key = key
        return _shared


def reset_structure_pool() -> None:
    """测试用: 丢掉共享实例池。"""
    global _shared, _shared_key
    with _shared_lock:
        _shared = None
        _shared_key = None


def _probe_health(url: str) -> bool:
    import httpx

    try:
        response = httpx.get(f"{url}/health", timeout=5.0)
    except Exception:  # noqa: BLE001
        return False
    return response.status_code == 200
