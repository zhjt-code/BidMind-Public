"""版面分析 CPU 推理封装。

职责: 懒加载版面检测模型(或回退的 PP-StructureV3 产线)并返回归一化块, 附带推理计数供诊断
不做: 不提供 OCR /predict、不识别标题文字、不占 GPU、不决定失败后的恢复
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import numpy as np
from loguru import logger

from cpu_quota import apply_cpu_thread_quota
from structure_blocks import blocks_from_layout_result, blocks_from_page_result

_LAYOUT_MODE = "layout"
_PPSTRUCTURE_MODE = "ppstructure"


class StructureEngine:
    """版面推理。默认只做版面检测(标题/表格框, 不做整页 OCR 与表格结构识别);
    STRUCTURE_ENGINE=ppstructure 回退到轻量 PP-StructureV3(公式/印章/图表/方向校正关闭)。

    模型创建与推理都在同一个专用线程上执行。Paddle predictor 只能在创建它的
    线程里使用, 而同步接口会被任意请求线程调用。
    """

    def __init__(self) -> None:
        mode = os.getenv("STRUCTURE_ENGINE", _LAYOUT_MODE).strip().lower()
        self.mode = _PPSTRUCTURE_MODE if mode == _PPSTRUCTURE_MODE else _LAYOUT_MODE
        self._pipeline: Any = None
        self._init_error: Optional[str] = None
        self.threads = 1
        self._started_at = time.monotonic()
        self.pages_ok = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.last_infer_ms = 0.0
        self.infer_thread = ""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="structure-infer")
        atexit.register(self._executor.shutdown, wait=False)

    def ensure_initialized(self) -> bool:
        if self._pipeline is not None:
            return True
        if self._init_error is not None:
            return False
        return bool(self._executor.submit(self._initialize).result())

    def _initialize(self) -> bool:
        self.infer_thread = threading.current_thread().name
        if self._pipeline is not None:
            return True
        if self._init_error is not None:
            return False
        try:
            self.threads = apply_cpu_thread_quota()
            self._pipeline = self._create_pipeline()
            logger.info(
                f"[版面服务] 就绪: mode={self.mode}, device=cpu, threads={self.threads}, "
                f"infer_thread={self.infer_thread}"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            logger.error(f"[版面服务] 初始化失败: {exc}")
            return False

    def _create_pipeline(self) -> Any:
        if self.mode == _PPSTRUCTURE_MODE:
            from paddleocr import PPStructureV3

            return PPStructureV3(
                device="cpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                use_seal_recognition=False,
                use_table_recognition=True,
                use_formula_recognition=False,
                use_chart_recognition=False,
                use_region_detection=False,
            )
        from paddleocr import LayoutDetection

        return LayoutDetection(
            model_name=os.getenv("STRUCTURE_LAYOUT_MODEL", "PP-DocLayout_plus-L"),
            device="cpu",
            cpu_threads=self.threads,
        )

    def predict(self, image: np.ndarray) -> list[dict[str, Any]]:
        if not self.ensure_initialized():
            return []
        return list(self._executor.submit(self._predict_on_worker, image).result())

    def _predict_on_worker(self, image: np.ndarray) -> list[dict[str, Any]]:
        self.infer_thread = threading.current_thread().name
        height, width = image.shape[:2]
        started = time.monotonic()
        try:
            pages = list(self._pipeline.predict(image))
        except Exception:
            self.failures += 1
            self.consecutive_failures += 1
            raise
        finally:
            self.last_infer_ms = (time.monotonic() - started) * 1000
        self.pages_ok += 1
        self.consecutive_failures = 0
        if not pages:
            return []
        to_blocks = blocks_from_page_result if self.mode == _PPSTRUCTURE_MODE else blocks_from_layout_result
        return to_blocks(pages[0], width=float(width), height=float(height))

    def stats(self) -> str:
        """诊断现场: 累计成功/失败、连续失败、上次推理耗时(不含排队)、运行时长、进程 RSS、推理线程。"""
        return (
            f"pages_ok={self.pages_ok}, failures={self.failures}, "
            f"consecutive={self.consecutive_failures}, infer_ms={self.last_infer_ms:.0f}, "
            f"uptime_s={time.monotonic() - self._started_at:.0f}, rss_mb={_rss_mb()}, "
            f"infer_thread={self.infer_thread}"
        )


def _rss_mb() -> int:
    """当前进程常驻内存(MB); 非 Linux 返回 -1。"""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return -1
