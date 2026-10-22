"""encode_gate.py 单元测试

覆盖：
- EmbeddingBatchSizeController 的 AIMD 行为（初始 / 加法慢升 / 乘法快降 / clamp）
- safe_encode_batch 的"单次 GPU 尝试后直接 CPU 兜底"路径
 (: 取消 3 次重试链, stall/OOM 首次失败即触发设备熔断 + CPU)
- task_cpu_sticky_scope 任务级 CPU 粘滞作用域
 (: ContextVar 在 ARQ worker job 边界传播 hard fallback 状态)
- GpuDeviceCooldownController 设备级冷却/熔断

设计约束：
- 不依赖 GPU，所有 encode 用 mock 替换
- 不依赖 OCR sidecar / 向量化模型实文件
- 不改全局 encode_lock 状态（safe_encode_batch 不获取锁）
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────────────────────────────────
#  控制器 AIMD 行为测试
# ──────────────────────────────────────────────────────────────────────


def _fresh_controller():
    """新建一个纯净的控制器实例（绕过进程级单例）。"""
    from app.core.vectorizer.encode_gate import EmbeddingBatchSizeController
    return EmbeddingBatchSizeController()


class TestEmbeddingBatchSizeController:
    def test_controller_initial_bs(self):
        """新实例的 current_bs 必须等于 INITIAL (12)。"""
        c = _fresh_controller()
        assert c.current_bs == 12
        assert c.min_bs == 4
        stats = c.get_stats()
        assert stats["current_bs"] == 12
        assert stats["degrade_total"] == 0
        assert stats["increase_total"] == 0

    def test_controller_additive_increase(self, monkeypatch):
        """连续 STABLE_SAMPLES=3 次成功 + 冷却已过 → bs += 2。"""
        c = _fresh_controller()
        # 把 cooldown 调成 0 方便测试不用等
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        # 第 1、2 次成功不加
        c.record_success(100)
        assert c.current_bs == 12
        c.record_success(100)
        assert c.current_bs == 12
        # 第 3 次触发加 2 → 14
        c.record_success(100)
        assert c.current_bs == 14
        # 加完后 streak 清零，再需要 3 次才会下一次加
        c.record_success(100)
        assert c.current_bs == 14
        c.record_success(100)
        assert c.current_bs == 14
        c.record_success(100)
        assert c.current_bs == 16

    def test_controller_additive_increase_respects_cooldown(self):
        """连续成功但冷却未过 → bs 不变。"""
        c = _fresh_controller()
        # 默认 cooldown = 15s，连续成功 5 次（立即）应该只触发一次加（第一次没有冷却）
        for _ in range(5):
            c.record_success(100)
        # 第一次达到 streak=3 时冷却窗口是 0，所以加了一次到 14
        # 之后 streak 清零，剩 2 次成功不够触发下一次
        assert c.current_bs == 14
        # 立即再来 3 次，但冷却未过 → 不加
        for _ in range(3):
            c.record_success(100)
        assert c.current_bs == 14

    def test_controller_multiplicative_decrease(self):
        """record_degrade → bs //= 2（且不低于 MIN）。"""
        c = _fresh_controller()
        # 先升到 16 方便看砍半效果
        c._current_bs = 16
        new_bs = c.record_degrade("stall")
        assert new_bs == 8
        assert c.current_bs == 8
        # 再砍一次到 4（MIN）
        new_bs = c.record_degrade("oom")
        assert new_bs == 4
        assert c.current_bs == 4

    def test_controller_clamp_min(self):
        """连续 degrade 后 bs 被 clamp 在 MIN=4，不会低于 4。"""
        c = _fresh_controller()
        for _ in range(10):
            c.record_degrade("stall")
        assert c.current_bs == 4
        stats = c.get_stats()
        assert stats["degrade_total"] == 10
        assert stats["last_degrade_reason"] == "stall"

    def test_controller_clamp_max(self, monkeypatch):
        """加法慢升被 clamp 在 MAX=32。"""
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        # 连续 100 次成功，每 3 次加 2，最终必然 clamp 到 32
        for _ in range(100):
            c.record_success(100)
        assert c.current_bs == 32

    def test_controller_degrade_resets_success_streak(self):
        """degrade 必须清零 success_streak，否则下次 success 会把统计污染。"""
        c = _fresh_controller()
        c.record_success(100)
        c.record_success(100)
        assert c.get_stats()["success_streak"] == 2
        c.record_degrade("stall")
        assert c.get_stats()["success_streak"] == 0

    def test_controller_max_bs_default_is_32(self):
        """settings 未覆盖时 _MAX 必须保持 32（与原硬编码一致，本地行为不变）。"""
        c = _fresh_controller()
        assert c._MAX == 32
        assert c.get_stats()["max_bs"] == 32

    def test_controller_increase_step_default_is_2(self):
        """settings 未覆盖时 _INCREASE_STEP 必须保持 2（本地行为不变）。"""
        c = _fresh_controller()
        assert c._INCREASE_STEP == 2
        assert c.get_stats()["increase_step"] == 2

    def test_controller_max_bs_overridden_by_settings(self, monkeypatch):
        """生产环境通过 env 覆盖 VECTOR_GPU_AIMD_MAX_BS=64 时 _MAX 应为 64。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_MAX_BS", 64, raising=False
        )
        c = _fresh_controller()
        assert c._MAX == 64
        assert c.get_stats()["max_bs"] == 64

    def test_controller_increase_step_overridden_by_settings(self, monkeypatch):
        """生产环境通过 env 覆盖 VECTOR_GPU_AIMD_INCREASE_STEP=4 时 _INCREASE_STEP 应为 4。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_INCREASE_STEP", 4, raising=False
        )
        c = _fresh_controller()
        assert c._INCREASE_STEP == 4
        assert c.get_stats()["increase_step"] == 4

    def test_controller_max_bs_clamped_below_min(self, monkeypatch):
        """误填 MAX < MIN 时应 clamp 为 _MIN，不让进程崩溃。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_MAX_BS", 1, raising=False
        )
        c = _fresh_controller()
        assert c._MAX == c._MIN  # clamp 为 4

    def test_controller_max_bs_clamped_above_hard_max(self, monkeypatch):
        """误填 MAX > HARD_MAX 时应 clamp 为 _HARD_MAX，防止 OOM。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_MAX_BS", 99999, raising=False
        )
        c = _fresh_controller()
        assert c._MAX == c._HARD_MAX

    def test_controller_increase_step_clamped_above_max(self, monkeypatch):
        """INCREASE_STEP > MAX 时应 clamp 到 MAX，防止一次升档跳过太多。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_MAX_BS", 8, raising=False
        )
        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_INCREASE_STEP", 100, raising=False
        )
        c = _fresh_controller()
        assert c._MAX == 8
        assert c._INCREASE_STEP == 8  # clamp 为 _MAX

    def test_controller_higher_max_actually_climbs_up(self, monkeypatch):
        """settings 覆盖 MAX=64 / STEP=4 时，控制器应能实际爬升到接近 MAX。"""
        from app.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_MAX_BS", 64, raising=False
        )
        monkeypatch.setattr(
            config_module.settings, "VECTOR_GPU_AIMD_INCREASE_STEP", 4, raising=False
        )
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        assert c.current_bs == c._INITIAL  # 起步 12
        for _ in range(100):
            c.record_success(100)
        assert c.current_bs == 64

    def test_controller_watermark_pause_does_not_increase_or_degrade(self, monkeypatch):
        """GPU 水位阻止升档时只暂停升档，不应触发降档或污染 fallback 语义。"""
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)

        c.record_success(100)
        c.record_success(100)
        c.record_success(
            100,
            increase_allowed=False,
            increase_pause_reason="watermark_exceeded",
        )

        stats = c.get_stats()
        assert c.current_bs == 12
        assert stats["success_streak"] == 0
        assert stats["increase_total"] == 0
        assert stats["degrade_total"] == 0
        assert stats["increase_pause_total"] == 1
        assert stats["last_increase_pause_reason"] == "watermark_exceeded"


# ──────────────────────────────────────────────────────────────────────
#  软降级（耗时感知）行为测试 -- 第八轮新增
#  目标：在不破坏现有 AIMD 行为的前提下，验证 elapsed_ms 与历史 p95
#       对比触发的软暂停 / 主动降档 / 冷启动跳过 / 冷却保护 / clamp 等行为
# ──────────────────────────────────────────────────────────────────────


def _enable_soft_degrade(monkeypatch, **overrides) -> None:
    """打开软降级开关并按需覆盖阈值；默认配置贴近生产保守值。"""
    from app.core import config as config_module

    defaults = {
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_ENABLED": True,
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_MIN_SAMPLES": 5,
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_PAUSE_RATIO": 2.0,
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_TRIGGER_RATIO": 3.0,
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_COOLDOWN_SEC": 30.0,
        "VECTOR_GPU_AIMD_SOFT_DEGRADE_STEP": 2,
        "VECTOR_GPU_AIMD_ELAPSED_WINDOW_SIZE": 60,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(config_module.settings, key, value, raising=False)


class TestEmbeddingBatchSizeControllerSoftDegrade:
    """覆盖耗时感知软降级的所有行为分支与回退保证。"""

    def test_soft_degrade_disabled_by_default(self):
        """开关默认关闭：极端 elapsed 不应该影响升档节奏，等同于现状。"""
        c = _fresh_controller()
        for _ in range(30):
            c.record_success(99999)
        stats = c.get_stats()
        assert stats["soft_degrade_total"] == 0
        assert stats["pause_total"] == 0
        assert c.current_bs >= c._INITIAL

    def test_soft_degrade_skipped_during_warmup(self, monkeypatch):
        """启用但样本不足 → 完全跳过软降级，走原逻辑。"""
        _enable_soft_degrade(monkeypatch, VECTOR_GPU_AIMD_SOFT_DEGRADE_MIN_SAMPLES=20)
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        for _ in range(5):
            c.record_success(99999)
        stats = c.get_stats()
        assert stats["soft_degrade_total"] == 0
        assert stats["pause_total"] == 0
        assert c.current_bs >= c._INITIAL

    def test_soft_degrade_pause_branch(self, monkeypatch):
        """elapsed 落在 (p95×2, p95×3] → 仅清零 streak，bs 不升不降。"""
        _enable_soft_degrade(monkeypatch)
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        for _ in range(10):
            c.record_success(100)
        bs_before = c.current_bs
        c.record_success(100 * 2 + 50)
        stats = c.get_stats()
        assert stats["soft_degrade_total"] == 0
        assert stats["pause_total"] >= 1
        assert c.current_bs == bs_before
        assert stats["success_streak"] == 0

    def test_soft_degrade_trigger_branch(self, monkeypatch):
        """elapsed > p95×3 → 主动降一档（step=2）。"""
        _enable_soft_degrade(monkeypatch)
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        for _ in range(10):
            c.record_success(100)
        bs_before = c.current_bs
        c.record_success(100 * 4)
        stats = c.get_stats()
        assert stats["soft_degrade_total"] == 1
        assert c.current_bs == max(c.min_bs, bs_before - 2)
        assert stats["success_streak"] == 0

    def test_soft_degrade_respects_cooldown(self, monkeypatch):
        """触发软降级后立刻再次触发 → 第二次被冷却保护，不再降。

        第二次的 elapsed 必须仍超过当前 p95 × trigger_ratio，
        否则不是被 cooldown 拦截，而是根本没命中阈值，测不到 cooldown 行为。
        第一次 elapsed=400 后，window 引入 400，新 p95 ≈ 400，
        所以第二次需要 elapsed > 400 × 3 = 1200 才能命中阈值。
        """
        _enable_soft_degrade(
            monkeypatch, VECTOR_GPU_AIMD_SOFT_DEGRADE_COOLDOWN_SEC=999.0
        )
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        for _ in range(10):
            c.record_success(100)
        c.record_success(100 * 4)
        bs_after_first = c.current_bs
        c.record_success(100 * 40)
        stats = c.get_stats()
        assert stats["soft_degrade_total"] == 1
        assert c.current_bs == bs_after_first

    def test_first_soft_degrade_not_blocked_by_boot_time(self, monkeypatch):
        """A fresh controller has no prior degradation even when uptime < cooldown."""
        _enable_soft_degrade(
            monkeypatch, VECTOR_GPU_AIMD_SOFT_DEGRADE_COOLDOWN_SEC=999.0
        )
        monkeypatch.setattr("app.core.vectorizer.encode_gate.time.monotonic", lambda: 100.0)
        c = _fresh_controller()
        for _ in range(10):
            c.record_success(100)
        c.record_success(400)
        assert c.get_stats()["soft_degrade_total"] == 1

    def test_soft_degrade_clamp_at_min(self, monkeypatch):
        """bs 已在 MIN，软降级不会跌破下限。

        注意：record_success 首次升档不受 _INCREASE_COOLDOWN_SEC 限制
        （初始 _last_increase_ts=0），所以 warmup 阶段的 elapsed=100
        仍可能让 bs 升离 MIN。warmup 后再次把 bs 钳回 MIN，
        以隔离测试目标（仅验证 trigger 分支的 clamp 行为）。
        """
        _enable_soft_degrade(monkeypatch)
        c = _fresh_controller()
        for _ in range(10):
            c.record_success(100)
        c._current_bs = c.min_bs
        c.record_success(100 * 4)
        stats = c.get_stats()
        assert c.current_bs == c.min_bs
        assert stats["soft_degrade_total"] == 1

    def test_soft_degrade_get_stats_fields(self, monkeypatch):
        """get_stats 必须暴露软降级相关新字段。"""
        _enable_soft_degrade(monkeypatch)
        c = _fresh_controller()
        for ms in (100, 200, 150):
            c.record_success(ms)
        stats = c.get_stats()
        for key in (
            "soft_degrade_total", "pause_total",
            "elapsed_window_size", "elapsed_p95_ms",
        ):
            assert key in stats
        assert stats["elapsed_window_size"] == 3
        assert stats["elapsed_p95_ms"] is not None

    def test_soft_degrade_normal_path_still_increases(self, monkeypatch):
        """正常 elapsed（不偏离 p95）→ 软降级分支不应阻塞原有升档。"""
        _enable_soft_degrade(monkeypatch)
        c = _fresh_controller()
        monkeypatch.setattr(c, "_INCREASE_COOLDOWN_SEC", 0.0)
        for _ in range(10):
            c.record_success(100)
        bs_before = c.current_bs
        for _ in range(c._STABLE_SAMPLES):
            c.record_success(110)
        assert c.current_bs > bs_before


# ──────────────────────────────────────────────────────────────────────
#  safe_encode_batch 集成测试（mock GPU / CPU encode）
# ──────────────────────────────────────────────────────────────────────


class _DummyEmbeddingModel:
    """可编程的 embedding model mock。

    通过 call_sequence 序列化地模拟每次 encode 的行为：
    - 'ok' → 返回 shape=(n, 8) 的 float32 数组
    - 'stall' → 睡眠比超时更长（模拟 GPU 卡住）
    - 'oom' → 抛 torch.cuda.OutOfMemoryError 风格的 RuntimeError
    """

    def __init__(
        self,
        call_sequence: list[str],
        stall_sleep_sec: float = 200.0,
        device: str = "cuda:0",
    ):
        self.call_sequence = list(call_sequence)
        self.stall_sleep_sec = stall_sleep_sec
        self.call_log: list[dict] = []
        # 真实 EmbeddingModel always 暴露 device; GPU 段据此如实打设备标签
        # mock 必须补齐该契约, 否则测不到真实分支
        self.device = device

    def encode(self, texts, batch_size, show_progress, progress_callback):
        # 记录每一次调用，供断言使用
        entry = {"bs": batch_size, "n": len(texts)}
        if not self.call_sequence:
            behavior = "ok"
        else:
            behavior = self.call_sequence.pop(0)
        entry["behavior"] = behavior
        self.call_log.append(entry)
        if behavior == "stall":
            # 必须睡够长让 asyncio.wait_for 先超时；真实 thread 会在此阻塞
            time.sleep(self.stall_sleep_sec)
            return np.zeros((len(texts), 8), dtype=np.float32)
        if behavior == "oom":
            raise RuntimeError("CUDA out of memory. fake oom")
        return np.random.randn(len(texts), 8).astype(np.float32)


@pytest.fixture
def reset_encode_gate_state():
    """每个 test 前后重置 encode_gate 全局状态，避免测试间污染。

    关键点：
    - 重置 batch size 控制器单例
    - 重置 GPU 设备级冷却/熔断控制器单例（第七轮改进引入）
    - 重置 CPU encode semaphore 单例 + metric handler（第八轮 CPU 自适应改造引入）
    - 清掉孤儿 future 引用
    - 替换 GPU encode 线程池为 max_workers=10（生产代码是 1），
      避免前批 stall 的 thread 堵住后批 submit（否则测不到"同批降批重试"）
    """
    import concurrent.futures

    from app.core.vectorizer import encode_gate

    # 重置控制器
    encode_gate._reset_batch_size_controller()
    encode_gate._reset_device_cooldown_controller()
    encode_gate._reset_cpu_encode_semaphore()
    encode_gate._reset_encode_shutdown_state_for_tests()
    encode_gate.set_cpu_metric_handler(None)
    old_gpu_admission_enabled = encode_gate.settings.GPU_ADMISSION_ENABLED
    old_gpu_min_free = encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB
    old_gpu_base = encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB
    old_gpu_per_1k = encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB
    old_gpu_repeated_fail = encode_gate.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD
    encode_gate.settings.GPU_ADMISSION_ENABLED = False
    # 防御: safe_encode_batch 现已读 EMBEDDING_DEVICE 决定主路径,
    # 测试 fixture 必须保存/恢复, 否则任一测试改写都会污染其他用例
    old_embedding_device = encode_gate.settings.EMBEDDING_DEVICE

    # 清孤儿
    with encode_gate._orphan_lock:
        encode_gate._last_orphan_future = None

    # 替换 executor 为多 worker，让 stall 的 thread 不堵塞后续 submit
    old_executor = encode_gate._gpu_encode_executor
    encode_gate._gpu_encode_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=10, thread_name_prefix="test-gpu-encode"
    )

    yield

    # 清理
    try:
        encode_gate._gpu_encode_executor.shutdown(wait=False)
    except Exception:
        pass
    encode_gate._gpu_encode_executor = old_executor
    encode_gate._reset_batch_size_controller()
    encode_gate._reset_device_cooldown_controller()
    encode_gate._reset_cpu_encode_semaphore()
    encode_gate._reset_encode_shutdown_state_for_tests()
    encode_gate.set_cpu_metric_handler(None)
    encode_gate.settings.GPU_ADMISSION_ENABLED = old_gpu_admission_enabled
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = old_gpu_min_free
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = old_gpu_base
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = old_gpu_per_1k
    encode_gate.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = old_gpu_repeated_fail
    encode_gate.settings.EMBEDDING_DEVICE = old_embedding_device
    with encode_gate._orphan_lock:
        encode_gate._last_orphan_future = None


@pytest.mark.asyncio
async def test_safe_encode_batch_happy_path(reset_encode_gate_state):
    """GPU 直接成功 → 走 GPU 路径返回，retry_chain 只有 1 条 ok 记录。"""
    from app.core.vectorizer import encode_gate

    mock_model = _DummyEmbeddingModel(call_sequence=["ok"])
    # encode_gate 内部 `from app.core.vectorizer.embedding import get_embedding_model` 是
    # 惰性 import，patch 原模块属性即可在运行时生效
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=mock_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a", "b", "c"] * 5,
            initial_batch_size=32,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (15, 8)
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert len(meta["retry_chain"]) == 1
    assert meta["retry_chain"][0]["result"] == "ok"
    # 控制器 current_bs 起点是 12，上限 min(32, 12, 15) = 12
    assert mock_model.call_log[0]["bs"] == 12


@pytest.mark.asyncio
async def test_safe_encode_batch_stall_no_retry_immediate_cpu(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: 单次 stall 即立刻 CPU 兜底, 不再做 attempt=2/3 重试。

 新合同:
 - GPU 段只调用 1 次 embedding_model.encode
 - retry_chain 内 stall 条目恰好 1 条, attempt 字段为 1
 - device_cooldown 首次 stall 即触发 (reason=stall_no_retry)
 - controller.record_degrade 仍然被调用 (让下批次起步小)
 - CPU 兜底成功返回, meta.device=cpu / degraded_to_cpu=True
 """
    from app.core.vectorizer import encode_gate

    # 缩短 stall 超时让测试快速通过
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    # 即便给 10 次 stall 也只应触发 1 次 GPU 调用
    gpu_model = _DummyEmbeddingModel(
        call_sequence=["stall"] * 10,
        stall_sleep_sec=1.0,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(20, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    controller_before = encode_gate._get_batch_size_controller().get_stats()
    device_cooldown = encode_gate._get_device_cooldown_controller()
    assert device_cooldown.is_in_cooldown() is False

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 20,
            initial_batch_size=32,
            batch_no=1,
            batch_count=1,
        )

    # GPU encode 只被调用 1 次, 不再尝试 attempt=2/3
    assert len(gpu_model.call_log) == 1, (
        f"GPU should be attempted exactly once, got {len(gpu_model.call_log)} calls"
    )
    # CPU 兜底接管
    assert result.shape == (20, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    cpu_model.encode.assert_called_once()

    # retry_chain 中 stall 仅 1 条, attempt=1
    stalls = [e for e in meta["retry_chain"] if e["result"] == "stall"]
    assert len(stalls) == 1
    assert stalls[0]["attempt"] == 1

    # 设备冷却被首次 stall 触发
    assert device_cooldown.is_in_cooldown() is True
    cooldown_stats = device_cooldown.get_stats()
    assert cooldown_stats["last_trigger_reason"] == "stall_no_retry"

    # AIMD 控制器仍记录 1 次降级, 让下批次起步更小
    controller_after = encode_gate._get_batch_size_controller().get_stats()
    assert controller_after["degrade_total"] == controller_before["degrade_total"] + 1


@pytest.mark.asyncio
async def test_safe_encode_batch_oom_no_retry_immediate_cpu(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: CUDA OOM 同样仅触发 1 次, 立即进入 CPU 兜底。"""
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    gpu_model = _DummyEmbeddingModel(
        call_sequence=["oom"] * 5,  # 即使给多次 oom 也只触发一次
        stall_sleep_sec=0.1,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(10, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    device_cooldown = encode_gate._get_device_cooldown_controller()

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 10,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
        )

    assert len(gpu_model.call_log) == 1
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    ooms = [e for e in meta["retry_chain"] if e["result"] == "oom"]
    assert len(ooms) == 1
    assert ooms[0]["attempt"] == 1
    assert device_cooldown.is_in_cooldown() is True
    assert device_cooldown.get_stats()["last_trigger_reason"] == "oom_no_retry"
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_safe_encode_batch_cpu_fallback_at_min(reset_encode_gate_state, monkeypatch):
    """GPU stall (无论 controller 起点) → 单次 stall 后立即 CPU 兜底。

 改造后, GPU 段只调用 1 次, 不再做 3 次重试链。
 """
    from app.core.vectorizer import encode_gate

    # 让 stall 超时很短
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    controller = encode_gate._get_batch_size_controller()
    controller._current_bs = 8

    # 构造一系列 stall（足够多次），让 GPU 循环耗尽
    stall_model = _DummyEmbeddingModel(
        call_sequence=["stall"] * 10,
        stall_sleep_sec=1.0,
    )

    # 用 mock 拦截 _get_cpu_fallback_model 返回一个直接成功的 model
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(10, 8).astype(np.float32)
    )
    monkeypatch.setattr(
        encode_gate, "_get_cpu_fallback_model", lambda: cpu_model
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=stall_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 10,
            initial_batch_size=32,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (10, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    # 至少触发过 stall
    stalls = [e for e in meta["retry_chain"] if e["result"] == "stall"]
    assert len(stalls) >= 1
    # CPU 模型被真的调用过
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_safe_encode_uses_batch_contract_on_stall(
    reset_encode_gate_state,
    monkeypatch,
):
    """旧 safe_encode 入口也必须复用 stall 超时与 CPU 兜底合同。"""
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    controller = encode_gate._get_batch_size_controller()
    controller._current_bs = 4

    stall_model = _DummyEmbeddingModel(
        call_sequence=["stall"],
        stall_sleep_sec=1.0,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(6, 8).astype(np.float32)
    )
    progress_calls: list[tuple[int, int]] = []

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=stall_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result = await encode_gate.safe_encode(
            texts=["t"] * 6,
            initial_batch_size=8,
            progress_callback=lambda done, total: progress_calls.append((done, total)),
        )

    assert result.shape == (6, 8)
    cpu_model.encode.assert_called_once()
    assert progress_calls[-1] == (6, 6)


@pytest.mark.asyncio
async def test_safe_encode_empty_input_reports_complete(reset_encode_gate_state):
    """空输入应直接返回空数组并触发完成回调，不访问模型。"""
    from app.core.vectorizer import encode_gate

    progress_calls: list[tuple[int, int]] = []
    result = await encode_gate.safe_encode(
        texts=[],
        initial_batch_size=8,
        progress_callback=lambda done, total: progress_calls.append((done, total)),
    )

    assert result.shape == (0, 0)
    assert progress_calls == [(0, 0)]


def test_estimate_cpu_fallback_timeout_scales_with_text_size(monkeypatch):
    """CPU fallback 超时应随文本规模增长并受上限约束。"""
    from app.core.vectorizer.encode_gate import estimate_cpu_fallback_timeout

    monkeypatch.setattr("app.core.vectorizer.encode_gate.CPU_FALLBACK_TIMEOUT_SEC", 120.0)
    monkeypatch.setattr("app.core.vectorizer.encode_gate.settings.VECTOR_CPU_FALLBACK_BASE_SEC", 45.0)
    monkeypatch.setattr("app.core.vectorizer.encode_gate.settings.VECTOR_CPU_FALLBACK_PER_1K_CHARS_SEC", 18.0)
    monkeypatch.setattr("app.core.vectorizer.encode_gate.settings.VECTOR_CPU_FALLBACK_MAX_SEC", 360.0)

    small = estimate_cpu_fallback_timeout(["a" * 500, "b" * 800])
    large = estimate_cpu_fallback_timeout(["a" * 6000] * 8)

    assert small >= 120.0
    assert large > small
    assert large <= 360.0


def test_adaptive_stall_timeout_expands_for_long_tail_text_batch(monkeypatch):
    """: 长尾批次文本突增时, GPU stall timeout 不应按小 batch 误缩短。"""
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 120.0)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 45.0)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MULTIPLIER", 5.0)
    monkeypatch.setattr(encode_gate, "_rolling_batch_durations", [24.5] * 20)

    timeout = encode_gate._get_adaptive_stall_timeout(
        batch_size=10,
        texts=["a" * 1295] * 5 + ["b" * 1566] * 4 + ["c" * 3755],
    )

    assert timeout >= 110.0


# 注: 旧测试 test_safe_encode_batch_keeps_retrying_when_controller_can_still_reduce
# 与 test_safe_encode_batch_retry_never_increases_attempt_batch_size 已删除:
# 它们绑定的"3 次同批重试"语义在 改造中被取消。
# 新行为由 test_safe_encode_batch_stall_no_retry_immediate_cpu 与
# test_safe_encode_batch_oom_no_retry_immediate_cpu 覆盖。


# ──────────────────────────────────────────────────────────────────────
#  GpuDeviceCooldownController & 设备级冷却集成测试
# ──────────────────────────────────────────────────────────────────────


class TestGpuDeviceCooldownController:
    def test_initial_state(self):
        from app.core.vectorizer.encode_gate import GpuDeviceCooldownController

        c = GpuDeviceCooldownController()
        assert c.is_in_cooldown() is False
        stats = c.get_stats()
        assert stats["consecutive_trigger"] == 0
        assert stats["total_triggered"] == 0

    def test_trigger_enters_cooldown(self):
        from app.core.vectorizer.encode_gate import GpuDeviceCooldownController

        c = GpuDeviceCooldownController(base_cooldown_sec=10.0)
        c.trigger("stall_at_min_bs")
        assert c.is_in_cooldown() is True
        stats = c.get_stats()
        assert stats["total_triggered"] == 1
        assert stats["consecutive_trigger"] == 1
        assert stats["last_trigger_reason"] == "stall_at_min_bs"

    def test_consecutive_trigger_exponential_backoff(self):
        from app.core.vectorizer.encode_gate import GpuDeviceCooldownController

        c = GpuDeviceCooldownController(base_cooldown_sec=2.0, max_cooldown_sec=100.0)
        c.trigger("a")
        remaining_1 = c.remaining_cooldown_sec()
        c.trigger("b")
        remaining_2 = c.remaining_cooldown_sec()
        # 第二次触发应以指数退避延长冷却窗口
        assert remaining_2 > remaining_1

    def test_record_success_clears_cooldown(self):
        from app.core.vectorizer.encode_gate import GpuDeviceCooldownController

        c = GpuDeviceCooldownController(base_cooldown_sec=5.0)
        c.trigger("stall_at_min_bs")
        assert c.is_in_cooldown() is True
        c.record_success()
        assert c.is_in_cooldown() is False
        stats = c.get_stats()
        assert stats["consecutive_trigger"] == 0
        assert stats["consecutive_orphan"] == 0

    def test_record_orphan_triggers_cooldown_at_threshold(self):
        from app.core.vectorizer.encode_gate import GpuDeviceCooldownController

        c = GpuDeviceCooldownController(base_cooldown_sec=5.0, orphan_trigger_count=2)
        assert c.record_orphan() is False  # 第 1 次不触发
        assert c.is_in_cooldown() is False
        assert c.record_orphan() is True   # 第 2 次触发
        assert c.is_in_cooldown() is True


@pytest.mark.asyncio
async def test_safe_encode_batch_stick_to_cpu_skips_gpu(reset_encode_gate_state):
    """stick_to_cpu=True 时应直接走 CPU 兜底，不触发 GPU encode。"""
    from app.core.vectorizer import encode_gate

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])  # 不应被调用
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(5, 8).astype(np.float32)
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 5,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
            stick_to_cpu=True,
        )

    assert result.shape == (5, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    assert meta["stick_to_cpu"] is True
    # 走 CPU，GPU 不应被触达
    assert len(gpu_model.call_log) == 0
    cpu_model.encode.assert_called_once()
    # retry_chain 应至少包含一次 skip_gpu
    skips = [e for e in meta["retry_chain"] if e["result"] == "skip_gpu"]
    assert any(e.get("reason") == "stick_to_cpu" for e in skips)


# ──────────────────────────────────────────────────────────────────────
# 防御: prefer_subprocess CPU 子进程主路径 (避免 GIL 阻塞 keepalive)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_encode_batch_prefer_subprocess_uses_process(
    reset_encode_gate_state, monkeypatch
):
    """: prefer_subprocess=True 时 CPU 段主路径走子进程, 不触达主进程线程模型。

 核心不变量: vectorize 大批量 CPU encode 必须离开主进程(子进程), 否则 encode
 持 GIL 会饿死向量化 keepalive 协程 -> Redis 心跳停滞 -> reconciler 误判
 running_heartbeat_stale 触发重复恢复(本 Bug 的根因)。若未来有人把主路径改回
 asyncio.to_thread, 本测试将失败以阻止回归。
 """
    from app.core.vectorizer import encode_gate

    proc_calls: list[dict] = []

    async def _fake_proc(texts, batch_size, device, timeout):
        proc_calls.append({"n": len(texts), "device": device})
        return np.random.randn(len(texts), 8).astype(np.float32)

    monkeypatch.setattr(encode_gate, "_run_in_encode_process", _fake_proc)

    # 主进程线程模型: 子进程成功时绝不应被调用
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(return_value=np.random.randn(5, 8).astype(np.float32))
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])  # stick_to_cpu 应跳过 GPU
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 5,
            initial_batch_size=16,
            batch_no=1,
            batch_count=3,
            stick_to_cpu=True,
            prefer_subprocess=True,
        )

    assert result.shape == (5, 8)
    assert meta["device"] == "cpu"
    assert meta["recovery_mode"] == "process"
    assert len(proc_calls) == 1
    assert proc_calls[0]["device"] == "cpu"
    cpu_model.encode.assert_not_called()
    assert len(gpu_model.call_log) == 0


@pytest.mark.asyncio
async def test_safe_encode_batch_prefer_subprocess_falls_back_to_thread(
    reset_encode_gate_state, monkeypatch
):
    """: 子进程不可用时回退主进程线程, 保证向量化不因子进程故障中断。"""
    from app.core.vectorizer import encode_gate

    async def _boom_proc(texts, batch_size, device, timeout):
        raise RuntimeError("subprocess pool broken")

    monkeypatch.setattr(encode_gate, "_run_in_encode_process", _boom_proc)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(return_value=np.random.randn(4, 8).astype(np.float32))
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 4,
            initial_batch_size=16,
            batch_no=1,
            batch_count=2,
            stick_to_cpu=True,
            prefer_subprocess=True,
        )

    assert result.shape == (4, 8)
    assert meta["device"] == "cpu"
    # 子进程失败 -> 线程兜底接管
    assert meta["recovery_mode"] == "thread"
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_safe_encode_batch_default_keeps_thread_primary(
    reset_encode_gate_state, monkeypatch
):
    """回归护栏: 默认 prefer_subprocess=False 时 CPU 主路径仍是线程, 不触达子进程。

    保证本次改动不影响检索/单条 encode 等高频小批量路径的原有行为。
    """
    from app.core.vectorizer import encode_gate

    proc_calls: list[int] = []

    async def _fake_proc(texts, batch_size, device, timeout):
        proc_calls.append(len(texts))
        return np.random.randn(len(texts), 8).astype(np.float32)

    monkeypatch.setattr(encode_gate, "_run_in_encode_process", _fake_proc)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(return_value=np.random.randn(5, 8).astype(np.float32))
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 5,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
            stick_to_cpu=True,
        )

    assert result.shape == (5, 8)
    assert meta["recovery_mode"] == "thread"
    cpu_model.encode.assert_called_once()
    assert proc_calls == []


# ──────────────────────────────────────────────────────────────────────
# 防御: EMBEDDING_DEVICE=cpu 主路径 (部署层显式指定 CPU)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cpu_main_path_skips_gpu_and_no_fallback_meta(reset_encode_gate_state):
    """ 防御: settings.EMBEDDING_DEVICE=cpu 时, safe_encode_batch 应直接走
 CPU 主路径, GPU 段完全跳过, 不应标记为 fallback / degraded_to_cpu。
 """
    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "cpu"

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(3, 8).astype(np.float32)
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["x", "y", "z"],
            initial_batch_size=8,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (3, 8)
    assert meta["device"] == "cpu"
    assert meta["cpu_main_path"] is True
    # 关键: 主路径不算 fallback / degrade
    assert meta["degraded_to_cpu"] is False
    assert meta["cpu_fallback_kind"] is None
    assert meta["sticky_cpu_eligible"] is False
    assert meta["fallback_reason"] is None
    # GPU 段完全不应被触达 (lock / admission / encode 全部跳过)
    assert len(gpu_model.call_log) == 0
    cpu_model.encode.assert_called_once()
    # retry_chain 第一条应为 cpu_main_path skip
    skips = [e for e in meta["retry_chain"] if e["result"] == "skip_gpu"]
    assert any(e.get("reason") == "cpu_main_path" for e in skips)


@pytest.mark.asyncio
async def test_cpu_main_path_does_not_set_context_var(reset_encode_gate_state):
    """ 防御: CPU 主路径下不写 _task_stick_to_cpu ContextVar
 (主路径本来就是 CPU, 不需要粘滞污染同 worker scope 后续判定)。
 """
    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "cpu"

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(2, 8).astype(np.float32)
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            assert encode_gate.is_task_cpu_sticky() is False
            await encode_gate.safe_encode_batch(
                texts=["a", "b"],
                initial_batch_size=8,
                batch_no=1,
                batch_count=1,
            )
            # 关键断言: 主路径不应写 ContextVar
            assert encode_gate.is_task_cpu_sticky() is False


@pytest.mark.asyncio
async def test_cpu_main_path_logs_at_debug_not_warning(
    reset_encode_gate_state, caplog
):
    """ 防御: CPU 主路径不得打印 WARNING 级别 'GPU 失败' 或
 INFO 级别 'CPU 回退/CPU 兜底' 日志, 避免误导运维。
 """
    import logging as _logging

    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "cpu"

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(3, 8).astype(np.float32)
    )

    # loguru 默认不输出到 logging caplog, 这里 sink 一份到 stdlib logging
    from loguru import logger as _loguru_logger

    sink_id = _loguru_logger.add(
        lambda message: _logging.getLogger("encode_gate_test").log(
            _logging.WARNING if "WARNING" in message.record["level"].name else _logging.INFO,
            message.record["message"],
        ),
        level="DEBUG",
    )
    try:
        with caplog.at_level(_logging.INFO, logger="encode_gate_test"):
            with patch(
                "app.core.vectorizer.embedding.get_embedding_model",
                return_value=gpu_model,
            ), patch.object(
                encode_gate, "_get_cpu_fallback_model", return_value=cpu_model
            ):
                await encode_gate.safe_encode_batch(
                    texts=["x", "y", "z"],
                    initial_batch_size=8,
                    batch_no=1,
                    batch_count=1,
                )

        all_msgs = " | ".join(r.message for r in caplog.records)
        # 主路径下不得出现"GPU 异常"/"GPU 失败"/"CPU 兜底"等误导文案
        assert "GPU 异常" not in all_msgs, all_msgs
        assert "GPU 失败" not in all_msgs, all_msgs
        assert "CPU 兜底" not in all_msgs, all_msgs
        assert "CPU 回退" not in all_msgs, all_msgs
    finally:
        _loguru_logger.remove(sink_id)


@pytest.mark.asyncio
async def test_cpu_main_path_logs_warning_when_real_gpu_failure(
    reset_encode_gate_state, monkeypatch
):
    """ 防御 (反向): EMBEDDING_DEVICE=auto/cuda 时, GPU stall/oom
 仍必须打 WARNING (确保我们没把 fallback 日志全消音)。
 """

    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "auto"

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    gpu_model = _DummyEmbeddingModel(
        call_sequence=["stall"], stall_sleep_sec=1.0
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(6, 8).astype(np.float32)
    )

    from loguru import logger as _loguru_logger

    captured: list[tuple[str, str]] = []
    sink_id = _loguru_logger.add(
        lambda message: captured.append(
            (message.record["level"].name, message.record["message"])
        ),
        level="DEBUG",
    )
    try:
        with patch(
            "app.core.vectorizer.embedding.get_embedding_model",
            return_value=gpu_model,
        ), patch.object(
            encode_gate, "_get_cpu_fallback_model", return_value=cpu_model
        ):
            _, meta = await encode_gate.safe_encode_batch(
                texts=["a"] * 6,
                initial_batch_size=8,
                batch_no=1,
                batch_count=1,
            )

        assert meta["cpu_main_path"] is False
        assert meta["degraded_to_cpu"] is True
        # 真异常路径必须有 WARNING 文案
        warnings = [m for lvl, m in captured if lvl == "WARNING"]
        assert any("GPU 异常" in m for m in warnings), warnings
    finally:
        _loguru_logger.remove(sink_id)


# ──────────────────────────────────────────────────────────────────────
# 防御: 任务级 CPU 粘滞 (ContextVar) 跨 encode 调用传播
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_cpu_sticky_scope_propagates_after_hard_fallback(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: scope 内首次 hard fallback 之后, 同 scope 后续 encode
 自动跳 GPU 直接走 CPU; 退出 scope 后 ContextVar 自动 reset。
 """
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    # 第一次 GPU stall (触发 hard fallback 并粘滞), 后续调用即使 mock 返回 ok 也不应触达 GPU
    gpu_model = _DummyEmbeddingModel(
        call_sequence=["stall", "ok", "ok"],
        stall_sleep_sec=1.0,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        side_effect=lambda texts, *_args, **_kw: np.random.randn(
            len(texts), 8
        ).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    with patch(
        "app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model
    ):
        with encode_gate.task_cpu_sticky_scope():
            # 进入 scope 时 ContextVar 必为 False
            assert encode_gate.is_task_cpu_sticky() is False

            # 第一次: GPU stall 触发 hard fallback + 回写 ContextVar
            _, meta1 = await encode_gate.safe_encode_batch(
                texts=["a"] * 6,
                initial_batch_size=8,
                batch_no=1,
                batch_count=3,
            )
            assert meta1["degraded_to_cpu"] is True
            assert meta1["sticky_cpu_eligible"] is True
            assert encode_gate.is_task_cpu_sticky() is True

            # 第二次 / 第三次: 应直接走 CPU, GPU encode 不再被触达
            gpu_calls_before = len(gpu_model.call_log)
            _, meta2 = await encode_gate.safe_encode_batch(
                texts=["b"] * 4,
                initial_batch_size=8,
                batch_no=2,
                batch_count=3,
            )
            _, meta3 = await encode_gate.safe_encode_batch(
                texts=["c"] * 3,
                initial_batch_size=8,
                batch_no=3,
                batch_count=3,
            )
            assert len(gpu_model.call_log) == gpu_calls_before, (
                "后续 encode 不应再触达 GPU"
            )

            for meta in (meta2, meta3):
                assert meta["device"] == "cpu"
                assert meta["context_stick_to_cpu"] is True
                skips = [e for e in meta["retry_chain"] if e["result"] == "skip_gpu"]
                assert any(e.get("reason") == "task_sticky_cpu" for e in skips)

        # 退出 scope 后 ContextVar 必须自动 reset
        assert encode_gate.is_task_cpu_sticky() is False


@pytest.mark.asyncio
async def test_task_cpu_sticky_scope_not_triggered_by_admission_soft(
    reset_encode_gate_state, monkeypatch
):
    """ 同源场景防御: admission soft fallback (insufficient_vram 单次)
 不应触发任务级粘滞 -- 否则一次预检失败会污染整个 ARQ job。
 """
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    monkeypatch.setattr(
        "app.core.gpu.admission.get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=8192,
            used_mb=7600,
            free_mb=592,
            utilization=90,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(5, 8).astype(np.float32)
    )
    gpu_model_proxy = MagicMock(side_effect=AssertionError("GPU model must not load"))

    with patch(
        "app.core.vectorizer.embedding.get_embedding_model", gpu_model_proxy
    ), patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            _, meta = await encode_gate.safe_encode_batch(
                texts=["t"] * 5,
                initial_batch_size=16,
                batch_no=1,
                batch_count=1,
            )
            # admission soft fallback: cpu_fallback_kind=soft, sticky_cpu_eligible=False
            assert meta["cpu_fallback_kind"] == "soft"
            assert meta["sticky_cpu_eligible"] is False
            # ContextVar 不应被污染
            assert encode_gate.is_task_cpu_sticky() is False


@pytest.mark.asyncio
async def test_task_cpu_sticky_scope_isolation_between_scopes(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: 两个连续 scope 互不污染; 内层嵌套 scope 退出后
 恢复外层粘滞状态 (token reset 正确)。
 """
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    # 场景 1: 第一个 scope 触发粘滞, 退出后第二个 scope 应是干净状态
    gpu_model = _DummyEmbeddingModel(
        call_sequence=["stall", "ok"],
        stall_sleep_sec=1.0,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        side_effect=lambda texts, *_args, **_kw: np.random.randn(
            len(texts), 8
        ).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    with patch(
        "app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model
    ):
        with encode_gate.task_cpu_sticky_scope():
            await encode_gate.safe_encode_batch(
                texts=["a"] * 3,
                initial_batch_size=8,
                batch_no=1,
                batch_count=1,
            )
            assert encode_gate.is_task_cpu_sticky() is True
        # 退出 scope 1: 清零
        assert encode_gate.is_task_cpu_sticky() is False

        # scope 2: 全新作用域, 此时设备已 cooldown 中, 仍会走 CPU 但
        # ContextVar 自身在 scope 入口仍是 False -> 验证 reset 干净
        with encode_gate.task_cpu_sticky_scope():
            assert encode_gate.is_task_cpu_sticky() is False

    # 场景 2: 嵌套 scope 入口 reset 后, 退出回到外层应仍然 False
    with encode_gate.task_cpu_sticky_scope():
        encode_gate._mark_task_sticky_cpu()
        assert encode_gate.is_task_cpu_sticky() is True
        with encode_gate.task_cpu_sticky_scope():
            assert encode_gate.is_task_cpu_sticky() is False
            encode_gate._mark_task_sticky_cpu()
            assert encode_gate.is_task_cpu_sticky() is True
        # 嵌套 scope 退出后应恢复外层粘滞状态
        assert encode_gate.is_task_cpu_sticky() is True
    # 最外层退出后清零
    assert encode_gate.is_task_cpu_sticky() is False


# ──────────────────────────────────────────────────────────────────────
# 防御: worker shutdown 优雅关闭 encode 子系统
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_cpu_encode(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: shutdown 触发后等 in-flight CPU encode 完成才返回。"""
    from app.core.vectorizer import encode_gate

    # mock cpu encode 用 time.sleep 模拟阻塞型工作 (典型 ONNX runtime 行为)
    encode_completed = asyncio.Event()

    def _slow_encode(texts, batch_size, show_progress, progress_callback):
        time.sleep(0.4)
        return np.random.randn(len(texts), 8).astype(np.float32)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(side_effect=_slow_encode)
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    async def _one_encode():
        try:
            await encode_gate.safe_encode_batch(
                texts=["a", "b"],
                initial_batch_size=4,
                batch_no=1,
                batch_count=1,
                stick_to_cpu=True,
            )
        finally:
            encode_completed.set()

    task = asyncio.create_task(_one_encode())
    # 等 in-flight 计数器观测到 >=1, 再触发 shutdown
    for _ in range(50):
        if encode_gate.get_active_encode_count() >= 1:
            break
        await asyncio.sleep(0.01)
    assert encode_gate.get_active_encode_count() >= 1, "in-flight 未被计数"

    stats = await encode_gate.shutdown_encode_workloads(timeout=5.0)
    # shutdown 应等到 encode 完成 (timeout=False)
    assert stats["timeout"] is False
    assert stats["active_count_at_exit"] == 0
    assert stats["active_count_at_start"] >= 1
    # encode task 已正常结束
    assert encode_completed.is_set()
    await task


@pytest.mark.asyncio
async def test_shutdown_timeout_warns_and_releases(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: 超时后返回 timeout=True, 排队 future 被强制 cancel。"""
    from app.core.vectorizer import encode_gate

    # mock cpu encode 模拟长任务 (远超 shutdown timeout)
    block_long = threading.Event()

    def _block_encode(texts, batch_size, show_progress, progress_callback):
        block_long.wait(timeout=5.0)
        return np.random.randn(len(texts), 8).astype(np.float32)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(side_effect=_block_encode)
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    encode_task = asyncio.create_task(
        encode_gate.safe_encode_batch(
            texts=["a"],
            initial_batch_size=4,
            batch_no=1,
            batch_count=1,
            stick_to_cpu=True,
        )
    )
    for _ in range(50):
        if encode_gate.get_active_encode_count() >= 1:
            break
        await asyncio.sleep(0.01)

    stats = await encode_gate.shutdown_encode_workloads(timeout=0.3)
    assert stats["timeout"] is True
    assert stats["active_count_at_exit"] >= 1
    assert stats["elapsed_sec"] >= 0.3

    # 放开阻塞让 thread 完成, 避免 fixture teardown 期间 thread 仍跑
    block_long.set()
    try:
        await asyncio.wait_for(encode_task, timeout=3.0)
    except asyncio.TimeoutError:
        pass


@pytest.mark.asyncio
async def test_safe_encode_rejects_after_shutdown_starts(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: shutdown 触发后, 新 encode 提交直接 raise EncodeShuttingDownError。"""
    from app.core.vectorizer import encode_gate

    # shutdown 时无 in-flight, 立即返回
    stats = await encode_gate.shutdown_encode_workloads(timeout=0.5)
    assert stats["timeout"] is False
    assert encode_gate.is_encode_shutting_down() is True

    # 后续 safe_encode_batch 调用必须 raise
    with pytest.raises(encode_gate.EncodeShuttingDownError):
        await encode_gate.safe_encode_batch(
            texts=["a"],
            initial_batch_size=4,
            batch_no=1,
            batch_count=1,
        )

    # safe_encode 兼容入口也应 raise (透传)
    with pytest.raises(encode_gate.EncodeShuttingDownError):
        await encode_gate.safe_encode(texts=["x"], initial_batch_size=1)


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(reset_encode_gate_state):
    """ 防御: 连续两次 shutdown 不报错, 第二次立即返回 already_shutdown 标记。"""
    from app.core.vectorizer import encode_gate

    stats1 = await encode_gate.shutdown_encode_workloads(timeout=0.5)
    stats2 = await encode_gate.shutdown_encode_workloads(timeout=0.5)

    assert stats1.get("already_shutdown") is not True
    assert stats2.get("already_shutdown") is True
    assert stats2["elapsed_sec"] == 0.0


@pytest.mark.asyncio
async def test_inflight_counter_decrements_on_exception(
    reset_encode_gate_state, monkeypatch
):
    """ 防御: encode 抛异常时 finally 路径仍 dec 计数, idle event 正确恢复。"""
    from app.core.vectorizer import encode_gate

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(side_effect=RuntimeError("simulated cpu encode fail"))
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)
    # 同时让子进程 fallback 也失败, 确保 finally 被命中
    monkeypatch.setattr(
        encode_gate,
        "_run_in_encode_process",
        MagicMock(side_effect=RuntimeError("simulated subprocess fail")),
    )

    assert encode_gate.get_active_encode_count() == 0
    with pytest.raises(RuntimeError):
        await encode_gate.safe_encode_batch(
            texts=["a"],
            initial_batch_size=4,
            batch_no=1,
            batch_count=1,
            stick_to_cpu=True,
        )
    # 异常路径走完后, in-flight 计数器必须归零
    assert encode_gate.get_active_encode_count() == 0
    # idle event 应仍为 set (允许 shutdown 立即通过)
    idle_event = encode_gate._get_all_idle_event()
    assert idle_event.is_set() is True


@pytest.mark.asyncio
async def test_safe_encode_batch_gpu_admission_low_vram_uses_cpu(
    reset_encode_gate_state,
    monkeypatch,
):
    """GPU admission 判定显存不足时不应触发 GPU encode。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    monkeypatch.setattr(
        "app.core.gpu.admission.get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=8192,
            used_mb=7600,
            free_mb=592,
            utilization=90,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(5, 8).astype(np.float32)
    )
    get_gpu_model = MagicMock(side_effect=AssertionError("GPU model must not load"))

    with patch("app.core.vectorizer.embedding.get_embedding_model", get_gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 5,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (5, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    assert meta["gpu_admission"]["reason"] == "insufficient_vram"
    assert meta["cpu_fallback_kind"] == "soft"
    assert meta["sticky_cpu_eligible"] is False
    get_gpu_model.assert_not_called()
    cpu_model.encode.assert_called_once()
    skips = [e for e in meta["retry_chain"] if e["result"] == "skip_gpu"]
    assert any(e.get("reason") == "gpu_admission:insufficient_vram" for e in skips)


@pytest.mark.asyncio
async def test_safe_encode_batch_inflight_full_does_not_stick_to_cpu(
    reset_encode_gate_state,
    monkeypatch,
):
    """许可争用是软回退: 本批走 CPU, 同任务后续批次仍可再试 GPU。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    monkeypatch.setattr(
        "app.core.gpu.admission.get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=16384,
            used_mb=2192,
            free_mb=14192,
            utilization=0,
            source="test",
        ),
    )

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return None, "", False

    monkeypatch.setattr("app.core.gpu.admission._acquire_gpu_permit", fake_acquire)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(5, 8).astype(np.float32)
    )
    get_gpu_model = MagicMock(side_effect=AssertionError("GPU model must not load"))

    with patch("app.core.vectorizer.embedding.get_embedding_model", get_gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            result, meta = await encode_gate.safe_encode_batch(
                texts=["t"] * 5,
                initial_batch_size=12,
                batch_no=5,
                batch_count=15,
            )
            assert encode_gate.is_task_cpu_sticky() is False

    assert result.shape == (5, 8)
    assert meta["device"] == "cpu"
    assert meta["gpu_admission"]["reason"] == "inflight_full"
    assert meta["cpu_fallback_kind"] == "soft"
    assert meta["sticky_cpu_eligible"] is False
    assert meta["admission_fail_streak"] == 0
    get_gpu_model.assert_not_called()


@pytest.mark.asyncio
async def test_safe_encode_batch_gpu_admission_retries_smaller_batch(
    reset_encode_gate_state,
    monkeypatch,
):
    """显存准入不足时应先降 batch_size 重估，低 batch 满足后仍走 GPU。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=7142,
            free_mb=1050,
            utilization=70,
            source="test",
        ),
    )

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return object(), f"{kind}:{owner}", True

    async def fake_release(redis, owner: str):  # noqa: ANN001
        return None

    monkeypatch.setattr(admission, "_acquire_gpu_permit", fake_acquire)
    monkeypatch.setattr(admission, "_release_gpu_permit", fake_release)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a" * 500] * 12,
            initial_batch_size=12,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (12, 8)
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert gpu_model.call_log[0]["bs"] == 8
    rejects = [
        item for item in meta["retry_chain"]
        if item["result"] == "admission_reject"
    ]
    assert rejects[0]["batch_size"] == 12
    assert meta["gpu_admission"]["batch_size"] == 8


@pytest.mark.asyncio
async def test_safe_encode_batch_admission_failure_is_soft_and_keeps_aimd(
    reset_encode_gate_state,
    monkeypatch,
):
    """单次 admission 失败只让当前批 CPU，不应污染 AIMD 降批统计。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    controller = encode_gate._get_batch_size_controller()
    before = controller.get_stats()

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=7492,
            free_mb=700,
            utilization=80,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(12, 8).astype(np.float32)
    )
    get_gpu_model = MagicMock(side_effect=AssertionError("GPU model must not load"))

    with patch("app.core.vectorizer.embedding.get_embedding_model", get_gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a" * 500] * 12,
            initial_batch_size=12,
            batch_no=1,
            batch_count=1,
        )

    after = controller.get_stats()
    assert result.shape == (12, 8)
    assert meta["device"] == "cpu"
    assert meta["cpu_fallback_kind"] == "soft"
    assert meta["sticky_cpu_eligible"] is False
    assert meta["admission_failed"] is True
    assert meta["admission_fail_streak"] == 1
    assert after["current_bs"] == before["current_bs"]
    assert after["degrade_total"] == before["degrade_total"]
    get_gpu_model.assert_not_called()
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_safe_encode_batch_resident_cuda_model_ignores_low_nvml_free(
    reset_encode_gate_state,
    monkeypatch,
):
    """CUDA 模型已驻留时，低 NVML free 多半是本进程缓存，应允许 GPU probe。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=8062,
            free_mb=130,
            utilization=40,
            source="test",
        ),
    )
    monkeypatch.setattr(encode_gate, "_cuda_embedding_model_loaded", lambda: True)

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return object(), f"{kind}:{owner}", True

    async def fake_release(redis, owner: str):  # noqa: ANN001
        return None

    monkeypatch.setattr(admission, "_acquire_gpu_permit", fake_acquire)
    monkeypatch.setattr(admission, "_release_gpu_permit", fake_release)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a" * 20] * 32,
            initial_batch_size=12,
            batch_no=2,
            batch_count=6,
        )

    assert result.shape == (32, 8)
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert meta["gpu_admission"]["reason"] == "resident_model_loaded"
    assert len(gpu_model.call_log) == 1


@pytest.mark.asyncio
async def test_safe_encode_batch_repeated_admission_failure_becomes_hard(
    reset_encode_gate_state,
    monkeypatch,
):
    """连续 admission 失败达到阈值后应升级为 hard fallback。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    encode_gate.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = 3

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=7492,
            free_mb=700,
            utilization=80,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(12, 8).astype(np.float32)
    )
    get_gpu_model = MagicMock(side_effect=AssertionError("GPU model must not load"))

    with patch("app.core.vectorizer.embedding.get_embedding_model", get_gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a" * 500] * 12,
            initial_batch_size=12,
            batch_no=3,
            batch_count=3,
            admission_fail_streak=2,
        )

    assert result.shape == (12, 8)
    assert meta["device"] == "cpu"
    assert meta["cpu_fallback_kind"] == "hard"
    assert meta["sticky_cpu_eligible"] is True
    assert meta["admission_fail_streak"] == 3
    assert meta["fallback_reason"] == "repeated_admission_fail"
    get_gpu_model.assert_not_called()
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_safe_encode_batch_gpu_admission_allows_gpu(
    reset_encode_gate_state,
    monkeypatch,
):
    """GPU admission 放行时仍应走 GPU encode，并在退出时释放 permit。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    released: list[str] = []
    monkeypatch.setattr(
        "app.core.gpu.admission.get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=24576,
            used_mb=2048,
            free_mb=22528,
            utilization=5,
            source="test",
        ),
    )

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return object(), f"{kind}:{owner}", True

    async def fake_release(redis, owner: str):  # noqa: ANN001
        released.append(owner)

    monkeypatch.setattr("app.core.gpu.admission._acquire_gpu_permit", fake_acquire)
    monkeypatch.setattr("app.core.gpu.admission._release_gpu_permit", fake_release)

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 5,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (5, 8)
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert meta["gpu_admission"]["action"] == "use_gpu"
    assert len(gpu_model.call_log) == 1
    assert released


@pytest.mark.asyncio
async def test_safe_encode_batch_watermark_pause_keeps_gpu_path(
    reset_encode_gate_state,
    monkeypatch,
):
    """GPU 成功但水位阻止升档时，本批仍是 GPU 成功，不应 CPU 兜底。"""
    from app.core.vectorizer import encode_gate

    controller = encode_gate._get_batch_size_controller()
    monkeypatch.setattr(controller, "_INCREASE_COOLDOWN_SEC", 0.0)
    controller.record_success(100)
    controller.record_success(100)

    monkeypatch.setattr(
        "app.core.vectorizer.gpu_watermark.should_allow_aimd_increase",
        lambda **_kwargs: SimpleNamespace(
            allow_increase=False,
            reason="watermark_exceeded",
            max_used_ratio=0.90,
            projected_used_ratio=0.93,
            snapshot_used_ratio=0.88,
            estimated_required_mb=512,
            free_mb=1200,
            total_mb=10000,
            to_dict=lambda: {
                "allow_increase": False,
                "reason": "watermark_exceeded",
                "max_used_ratio": 0.90,
                "projected_used_ratio": 0.93,
                "snapshot_used_ratio": 0.88,
                "estimated_required_mb": 512,
                "free_mb": 1200,
                "total_mb": 10000,
            },
        ),
    )

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(14, 8).astype(np.float32)
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 14,
            initial_batch_size=14,
            batch_no=1,
            batch_count=1,
        )

    stats = controller.get_stats()
    assert result.shape == (14, 8)
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert meta["gpu_aimd_watermark"]["reason"] == "watermark_exceeded"
    assert stats["current_bs"] == 12
    assert stats["increase_pause_total"] == 1
    cpu_model.encode.assert_not_called()


@pytest.mark.asyncio
async def test_safe_encode_batch_respects_device_cooldown(reset_encode_gate_state):
    """设备冷却期间应直接 CPU 兜底。"""
    from app.core.vectorizer import encode_gate

    # 手动触发冷却
    cooldown = encode_gate._get_device_cooldown_controller()
    cooldown.trigger("stall_at_min_bs")
    assert cooldown.is_in_cooldown() is True

    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"])
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(4, 8).astype(np.float32)
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 4,
            initial_batch_size=16,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (4, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    # GPU 不应被触达（冷却命中）
    assert len(gpu_model.call_log) == 0
    skips = [e for e in meta["retry_chain"] if e["result"] == "skip_gpu"]
    assert any(e.get("reason") == "device_cooldown" for e in skips)
    assert meta["device_cooldown_sec"] >= 0


@pytest.mark.asyncio
async def test_safe_encode_batch_cpu_fallback_triggers_device_cooldown(
    reset_encode_gate_state,
    monkeypatch,
):
    """GPU stall 进入 CPU 兜底时, 设备冷却控制器应被立即触发 (后)。"""
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.3)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    controller = encode_gate._get_batch_size_controller()
    controller._current_bs = 4  # 已在 MIN

    stall_model = _DummyEmbeddingModel(
        call_sequence=["stall"] * 5,
        stall_sleep_sec=1.0,
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(6, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    device_cooldown = encode_gate._get_device_cooldown_controller()
    assert device_cooldown.is_in_cooldown() is False

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=stall_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 6,
            initial_batch_size=8,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (6, 8)
    assert meta["degraded_to_cpu"] is True
    # 设备冷却应该被触发
    assert device_cooldown.is_in_cooldown() is True
    stats = device_cooldown.get_stats()
    assert stats["total_triggered"] >= 1


# ──────────────────────────────────────────────────────────────────────
#  第八轮 CPU 自适应改造: 锁解耦 + CPU 路径并发 + metric handler
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_caller_must_not_hold_encode_lock(reset_encode_gate_state, monkeypatch):
    """新契约: 调用方持有 encode_lock 时再调 safe_encode_batch GPU 段会死锁,
    即调用方必须不持锁。本用例反向验证: 不持锁可正常完成。
    """
    from app.core.vectorizer import encode_gate

    mock_model = _DummyEmbeddingModel(call_sequence=["ok"])
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=mock_model):
        # 调用方完全不持锁
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a", "b"],
            initial_batch_size=8,
            batch_no=1,
            batch_count=1,
        )
    assert result.shape == (2, 8)
    assert meta["device"] == "gpu"
    # GPU 锁等待时长字段必须存在 (即便为 0)
    assert "gpu_lock_wait_ms" in meta


@pytest.mark.asyncio
async def test_cpu_path_does_not_acquire_gpu_lock(reset_encode_gate_state, monkeypatch):
    """stick_to_cpu=True 时 CPU 路径必须不获取 encode_lock,
    通过 'lock 已被外部独占' 仍能成功完成来证明。
    """
    from app.core.vectorizer import encode_gate

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(3, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    # 模拟外部已持 GPU lock; CPU 路径不应被它阻塞
    gpu_lock = encode_gate.get_encode_lock()
    await gpu_lock.acquire()
    try:
        result, meta = await asyncio.wait_for(
            encode_gate.safe_encode_batch(
                texts=["x", "y", "z"],
                initial_batch_size=8,
                batch_no=1,
                batch_count=1,
                stick_to_cpu=True,
            ),
            timeout=5.0,
        )
    finally:
        gpu_lock.release()

    assert result.shape == (3, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_cpu_path_concurrent_routes_run_in_parallel(
    reset_encode_gate_state,
    monkeypatch,
):
    """CPU 路径同时多协程调 safe_encode_batch, 总耗时应近似单次耗时,
    证明实测推荐的 concurrency=2 能真正并行, 而非 capacity=1 串行。
    """
    from app.core.vectorizer import encode_gate

    # 真模型基准确认共享 ONNX session 的 sweet spot 是 concurrency=2,
    # 这里验证默认推荐容量下 2 路 CPU fallback 不会退化为串行。
    encode_gate._resize_cpu_encode_semaphore(2)

    per_call_sleep = 0.3

    def _slow_encode(texts, batch_size, show_progress, progress_callback):
        time.sleep(per_call_sleep)
        return np.zeros((len(texts), 8), dtype=np.float32)

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(side_effect=_slow_encode)
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    async def _one_call(idx: int):
        result, meta = await encode_gate.safe_encode_batch(
            texts=[f"t{idx}-1", f"t{idx}-2"],
            initial_batch_size=8,
            batch_no=idx,
            batch_count=3,
            stick_to_cpu=True,
        )
        assert result.shape == (2, 8)
        return meta

    t0 = time.perf_counter()
    metas = await asyncio.gather(_one_call(0), _one_call(1))
    elapsed = time.perf_counter() - t0

    # 串行 2 路至少需要 2*per_call_sleep, 并行只需 ~per_call_sleep + 调度开销
    assert elapsed < per_call_sleep * 2.0, (
        f"CPU 路径未并行: elapsed={elapsed:.3f}s, expected < {per_call_sleep * 2.0:.3f}s"
    )
    for meta in metas:
        assert meta["device"] == "cpu"


@pytest.mark.asyncio
async def test_cpu_path_records_metric_to_handler(reset_encode_gate_state, monkeypatch):
    """CPU 段成功 / 失败都应通过 set_cpu_metric_handler 注册的 handler 推送 metric。"""
    from app.core.vectorizer import encode_gate

    received: list[encode_gate.CpuEncodeMetric] = []
    encode_gate.set_cpu_metric_handler(lambda m: received.append(m))

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(2, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    result, meta = await encode_gate.safe_encode_batch(
        texts=["a", "b"],
        initial_batch_size=4,
        batch_no=1,
        batch_count=1,
        stick_to_cpu=True,
    )
    assert result.shape == (2, 8)
    assert len(received) == 1
    metric = received[0]
    assert metric.success is True
    assert metric.batch_size > 0
    assert metric.total_chars >= 2
    assert metric.latency_ms >= 0


@pytest.mark.asyncio
async def test_cpu_path_meta_carries_queue_wait_ms(reset_encode_gate_state, monkeypatch):
    """meta['cpu_queue_wait_ms'] 必须存在, 用于上层观测。"""
    from app.core.vectorizer import encode_gate

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(2, 8).astype(np.float32)
    )
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    _, meta = await encode_gate.safe_encode_batch(
        texts=["a", "b"],
        initial_batch_size=4,
        batch_no=1,
        batch_count=1,
        stick_to_cpu=True,
    )
    assert "cpu_queue_wait_ms" in meta
    assert meta["cpu_queue_wait_ms"] >= 0
    assert meta["gpu_lock_wait_ms"] == 0  # 未走 GPU 段


# ──────────────────────────────────────────────────────────────────────
# 防御: GPU 段设备标签必须取模型真实所在设备
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gpu_segment_reports_cpu_when_model_not_on_cuda(reset_encode_gate_state):
    """ 防御: torch 被装成 CPU-only 构建时模型落在 CPU,
 GPU 段即便执行成功也必须如实标 device=cpu, 不得谎报 gpu。
 """
    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "auto"
    # CPU-only torch 下 EmbeddingModel._resolve_device 解析结果就是 cpu
    cpu_built_model = _DummyEmbeddingModel(call_sequence=["ok"], device="cpu")

    with patch(
        "app.core.vectorizer.embedding.get_embedding_model",
        return_value=cpu_built_model,
    ):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["a", "b", "c"],
            initial_batch_size=8,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (3, 8)
    # GPU 段确实执行了 (不是 fallback), 但设备标签必须如实
    assert len(cpu_built_model.call_log) == 1
    assert meta["device"] == "cpu"
    assert meta["gpu_segment_on_cpu"] is True
    # 这不是运行时降级, 不得污染 fallback 语义
    assert meta["degraded_to_cpu"] is False
    assert meta["cpu_fallback_kind"] is None
    assert meta["cpu_main_path"] is False


@pytest.mark.asyncio
async def test_gpu_segment_reports_gpu_when_model_on_cuda(reset_encode_gate_state):
    """ 反向防御: 模型真在 CUDA 上时仍须标 device=gpu,
 防止设备标签修正过度, 把正常 GPU 批次误标成 cpu。
 """
    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "auto"
    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"], device="cuda:0")

    with patch(
        "app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model
    ):
        _, meta = await encode_gate.safe_encode_batch(
            texts=["a", "b", "c"],
            initial_batch_size=8,
            batch_no=1,
            batch_count=1,
        )

    assert meta["device"] == "gpu"
    assert "gpu_segment_on_cpu" not in meta


@pytest.mark.asyncio
async def test_gpu_segment_success_log_carries_real_device(
    reset_encode_gate_state, caplog
):
    """ 防御: 成功日志里的 device= 必须是模型真实设备。

 本次故障中 EmbeddingModel 打 device=cpu 而 safe_encode_batch 打
 device=gpu, 矛盾日志把排查方向带偏, 因此日志文案本身需要断言。
 """
    import logging as _logging

    from loguru import logger as _loguru_logger

    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "auto"
    cpu_built_model = _DummyEmbeddingModel(call_sequence=["ok"], device="cpu")

    sink_id = _loguru_logger.add(
        lambda message: _logging.getLogger("encode_gate_bug840").info(
            message.record["message"]
        ),
        level="DEBUG",
    )
    try:
        with caplog.at_level(_logging.INFO, logger="encode_gate_bug840"):
            with patch(
                "app.core.vectorizer.embedding.get_embedding_model",
                return_value=cpu_built_model,
            ):
                await encode_gate.safe_encode_batch(
                    texts=["a", "b", "c"],
                    initial_batch_size=8,
                    batch_no=1,
                    batch_count=1,
                )

        batch_logs = [
            r.message for r in caplog.records if "[safe_encode_batch] batch=" in r.message
        ]
        assert batch_logs, "未捕获到 safe_encode_batch 批次日志"
        assert all("device=cpu" in msg for msg in batch_logs), batch_logs
        assert not any("device=gpu" in msg for msg in batch_logs), batch_logs
    finally:
        _loguru_logger.remove(sink_id)


# ──────────────────────────────────────────────────────────────────────
# 粘滞 CPU 自愈回路
#  形态 A: 共享加速资源的降级粘滞是单向 latch, 资源恢复后无自动回收
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sticky_cpu_recovers_when_vram_returns_after_admission_fail(
    reset_encode_gate_state, monkeypatch
):
    """ 夹具 1 (本案例实体): admission 连败粘滞后显存恢复 -> 下一批 GPU 成功 -> sticky_cpu_cleared 置位、ContextVar 复位。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    encode_gate.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = 3

    # 第一次: 显存紧 (<min_free+estimated), 触发 admission 失败
    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=8000,
            free_mb=192,
            utilization=99,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(8, 8).astype(np.float32)
    )
    gpu_model = _DummyEmbeddingModel(call_sequence=["ok"], device="cuda:0")

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            # 第一批: admission 连败 (streak=2 起步, 触发 repeated_admission_fail 硬粘滞)
            _, meta1 = await encode_gate.safe_encode_batch(
                texts=["a" * 500] * 8,
                initial_batch_size=8,
                batch_no=1,
                batch_count=2,
                admission_fail_streak=2,
            )
            assert meta1["degraded_to_cpu"] is True
            assert meta1["cpu_fallback_kind"] == "hard"
            assert meta1["fallback_reason"] == "repeated_admission_fail"
            assert encode_gate.is_task_cpu_sticky() is True

            # 第二批: 显存恢复 (free_mb=4000 > min_free 512 + estimated), 触发自愈探查
            monkeypatch.setattr(
                admission,
                "get_gpu_snapshot",
                lambda: admission.GpuSnapshot(
                    total_mb=8192,
                    used_mb=4000,
                    free_mb=4192,
                    utilization=50,
                    source="test",
                ),
            )

            gpu_model = _DummyEmbeddingModel(call_sequence=["ok"], device="cuda:0")
            with patch(
                "app.core.vectorizer.embedding.get_embedding_model",
                return_value=gpu_model,
            ):
                _, meta2 = await encode_gate.safe_encode_batch(
                    texts=["b" * 500] * 8,
                    initial_batch_size=8,
                    batch_no=2,
                    batch_count=2,
                    admission_fail_streak=0,
                )

            # 自愈断言
            assert meta2.get("sticky_probe") is True
            assert meta2.get("sticky_cpu_cleared") is True
            assert encode_gate.is_task_cpu_sticky() is False, "ContextVar 必须在 GPU 成功后复位"
            assert gpu_model.call_log, "GPU 应被真实调用"


@pytest.mark.asyncio
async def test_sticky_cpu_holds_during_cooldown_even_if_vram_recovered(
    reset_encode_gate_state, monkeypatch
):
    """ 夹具 2 (同形态第二例, stall 实体): stall 触发粘滞后, cooldown 期内即使显存恢复也不探查, 尊重退避防振荡。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 512
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", 0.1)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", 0.2)
    monkeypatch.setattr(encode_gate, "_stash_orphan", lambda fut: None)

    gpu_model = _DummyEmbeddingModel(
        call_sequence=["stall", "ok"],
        stall_sleep_sec=1.0,
        device="cuda:0",
    )
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(6, 8).astype(np.float32)
    )

    # 第一批用充足显存 + 触发 stall: 不走 admission 拒, 而是 stall 触发硬粘滞 + cooldown
    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=4000,
            free_mb=4192,
            utilization=50,
            source="test",
        ),
    )

    get_gpu_model = MagicMock(return_value=gpu_model)

    with patch("app.core.vectorizer.embedding.get_embedding_model", get_gpu_model), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            _, meta1 = await encode_gate.safe_encode_batch(
                texts=["a", "b", "c", "d", "e", "f"],
                initial_batch_size=6,
                batch_no=1,
                batch_count=3,
            )
            assert meta1["degraded_to_cpu"] is True
            assert encode_gate.is_task_cpu_sticky() is True

            # 显存仍充足, 但 device_cooldown 已被 stall 触发 (cooldown_sec=60 默认).
            # 即使显存足够也不应探查, 走 CPU 路径.
            gpu_calls_before = len(gpu_model.call_log)
            _, meta2 = await encode_gate.safe_encode_batch(
                texts=["g", "h", "i", "j", "k", "l"],
                initial_batch_size=6,
                batch_no=2,
                batch_count=3,
            )
            assert meta2.get("sticky_probe") is not True, (
                "cooldown 期内即便显存充足也不应触发自愈探查"
            )
            assert len(gpu_model.call_log) == gpu_calls_before, (
                "cooldown 期内 GPU encode 不应被触达"
            )
            assert meta2["device"] == "cpu"


@pytest.mark.asyncio
async def test_sticky_cpu_skips_probe_when_vram_still_low(
    reset_encode_gate_state, monkeypatch
):
    """ 夹具 3 (零成本保持): 显存持续不足时粘滞路径不发起任何 GPU 调用。"""
    from app.core.gpu import admission
    from app.core.vectorizer import encode_gate

    encode_gate.settings.GPU_ADMISSION_ENABLED = True
    encode_gate.settings.GPU_ADMISSION_MIN_FREE_MB = 1024
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    encode_gate.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    encode_gate.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = 3

    # 持续低显存
    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=8000,
            free_mb=192,
            utilization=99,
            source="test",
        ),
    )

    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(
        return_value=np.random.randn(6, 8).astype(np.float32)
    )
    gpu_model_proxy = MagicMock(
        side_effect=AssertionError("GPU 不应在显存不足时被触达")
    )

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model_proxy), \
         patch.object(encode_gate, "_get_cpu_fallback_model", return_value=cpu_model):
        with encode_gate.task_cpu_sticky_scope():
            # 第一批: admission 连败粘滞 (streak=2 起步, 触发 hard)
            _, meta1 = await encode_gate.safe_encode_batch(
                texts=["a" * 500] * 6,
                initial_batch_size=6,
                batch_no=1,
                batch_count=2,
                admission_fail_streak=2,
            )
            assert meta1["degraded_to_cpu"] is True
            assert encode_gate.is_task_cpu_sticky() is True

            # 第二批: 显存仍低, 粘滞复检判定 False, 维持零成本 CPU 路径
            _, meta2 = await encode_gate.safe_encode_batch(
                texts=["b" * 500] * 6,
                initial_batch_size=6,
                batch_no=2,
                batch_count=2,
                admission_fail_streak=0,
            )
            assert meta2.get("sticky_probe") is not True, (
                "显存仍低时不应触发自愈探查"
            )
            assert meta2["device"] == "cpu"
            assert encode_gate.is_task_cpu_sticky() is True, (
                "粘滞应保持直至 GPU 真正成功"
            )


def _pin_stall_timeout(monkeypatch, seconds: float) -> None:
    """把自适应 stall 超时钉死, 避免滚动 p95 把测试拖进真实等待。"""
    from app.core.vectorizer import encode_gate

    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_SEC", seconds)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_MIN_SEC", seconds)
    monkeypatch.setattr(encode_gate, "BATCH_STALL_TIMEOUT_COLD_SEC", seconds)


def _wrap_wait_for_offload_started(monkeypatch) -> threading.Event:
    """在进入卸载等待时置位, 便于测试在墙钟超时回调之前堵住事件循环。"""
    from app.core.vectorizer import encode_gate

    entered = threading.Event()
    real_wait = encode_gate.wait_for_offload

    async def wrapped(future, timeout_sec, observation):
        entered.set()
        return await real_wait(future, timeout_sec, observation)

    monkeypatch.setattr(encode_gate, "wait_for_offload", wrapped)
    return entered


@pytest.mark.asyncio
async def test_gpu_fast_device_ignores_blocked_loop_wall_clock(
    reset_encode_gate_state, monkeypatch
):
    """ 本案例: 设备很快完成, 事件循环被堵住导致墙钟越过 stall 阈值。

 不得记 stall、不得冷却、不得把墙钟写入滚动耗时。
 """
    from app.core.vectorizer import encode_gate

    _pin_stall_timeout(monkeypatch, 0.05)
    encode_gate._rolling_batch_durations.clear()
    entered = _wrap_wait_for_offload_started(monkeypatch)
    mock_model = _DummyEmbeddingModel(call_sequence=["ok"], stall_sleep_sec=0.02)
    # ok 分支不睡眠; 用自定义 encode 固定一个很短的设备耗时
    def encode(texts, batch_size, show_progress, progress_callback):
        time.sleep(0.02)
        return np.zeros((len(texts), 8), dtype=np.float32)

    mock_model.encode = encode
    controller_before = encode_gate._get_batch_size_controller().get_stats()

    async def run():
        task = asyncio.create_task(
            encode_gate.safe_encode_batch(
                texts=["短查询"],
                initial_batch_size=32,
                batch_no=1,
                batch_count=1,
            )
        )
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set(), "GPU 等待未开始"
        time.sleep(0.2)
        return await task

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=mock_model):
        started = time.perf_counter()
        result, meta = await run()
        wall_sec = time.perf_counter() - started

    assert wall_sec > 0.15
    assert result.shape[0] == 1
    assert meta["device"] == "gpu"
    assert meta["degraded_to_cpu"] is False
    assert all(entry["result"] != "stall" for entry in meta["retry_chain"])
    assert meta["retry_chain"][-1]["elapsed_ms"] < 100
    assert encode_gate._get_device_cooldown_controller().is_in_cooldown() is False
    after = encode_gate._get_batch_size_controller().get_stats()
    assert after["degrade_total"] == controller_before["degrade_total"]
    assert encode_gate._rolling_batch_durations
    assert max(encode_gate._rolling_batch_durations) < 0.1


@pytest.mark.asyncio
async def test_cpu_thread_fast_device_does_not_fallback_on_blocked_loop(
    reset_encode_gate_state, monkeypatch
):
    """ 同形态第二例: CPU 线程路径同样不得把循环阻塞当成设备超时。"""
    from app.core.vectorizer import encode_gate

    encode_gate.settings.EMBEDDING_DEVICE = "cpu"
    monkeypatch.setattr(encode_gate, "estimate_cpu_fallback_timeout", lambda texts: 0.05)
    entered = _wrap_wait_for_offload_started(monkeypatch)
    metrics: list = []
    encode_gate.set_cpu_metric_handler(metrics.append)

    def encode(texts, batch_size, show_progress, progress_callback):
        time.sleep(0.02)
        return np.zeros((len(texts), 8), dtype=np.float32)

    cpu_model = MagicMock()
    cpu_model.encode = encode
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    async def forbid_process(*args, **kwargs):
        raise AssertionError("调度延迟不得降级到子进程")

    monkeypatch.setattr(encode_gate, "_run_in_encode_process", forbid_process)

    async def run():
        task = asyncio.create_task(
            encode_gate.safe_encode_batch(
                texts=["短查询"],
                initial_batch_size=8,
                batch_no=1,
                batch_count=1,
            )
        )
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set(), "CPU 等待未开始"
        time.sleep(0.2)
        return await task

    started = time.perf_counter()
    result, meta = await run()
    wall_sec = time.perf_counter() - started

    assert wall_sec > 0.15
    assert result.shape[0] == 1
    assert meta["device"] == "cpu"
    assert meta["recovery_mode"] == "thread"
    assert metrics, "线程成功必须留下设备内耗时"
    assert metrics[-1].success is True
    assert metrics[-1].latency_ms < 100
    assert all(item.success for item in metrics)


@pytest.mark.asyncio
async def test_real_gpu_device_stall_still_falls_back(
    reset_encode_gate_state, monkeypatch
):
    """设备内耗时真正越过预算时, 仍按 立即冷却并 CPU 兜底。"""
    from app.core.vectorizer import encode_gate

    _pin_stall_timeout(monkeypatch, 0.05)
    gpu_model = _DummyEmbeddingModel(call_sequence=["stall"], stall_sleep_sec=1.0)
    cpu_model = MagicMock()
    cpu_model.encode = MagicMock(return_value=np.zeros((4, 8), dtype=np.float32))
    monkeypatch.setattr(encode_gate, "_get_cpu_fallback_model", lambda: cpu_model)

    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=gpu_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["t"] * 4,
            initial_batch_size=32,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape == (4, 8)
    assert meta["device"] == "cpu"
    assert meta["degraded_to_cpu"] is True
    stalls = [entry for entry in meta["retry_chain"] if entry["result"] == "stall"]
    assert len(stalls) == 1
    assert stalls[0]["timeout_cause"] == "device_stall"
    assert encode_gate._get_device_cooldown_controller().is_in_cooldown() is True
    cpu_model.encode.assert_called_once()


@pytest.mark.asyncio
async def test_gpu_queued_timeout_does_not_declare_device_fault(
    reset_encode_gate_state, monkeypatch
):
    """执行器队列未开始就算墙钟超时, 也不得判设备故障; grace 内完成保持 GPU。"""
    import concurrent.futures

    from app.core.vectorizer import encode_gate

    _pin_stall_timeout(monkeypatch, 0.15)
    previous = encode_gate._gpu_encode_executor
    single = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="queued-gpu")
    encode_gate._gpu_encode_executor = single
    previous.shutdown(wait=False)
    single.submit(lambda: time.sleep(0.2))

    mock_model = _DummyEmbeddingModel(call_sequence=["ok"])
    controller_before = encode_gate._get_batch_size_controller().get_stats()
    with patch("app.core.vectorizer.embedding.get_embedding_model", return_value=mock_model):
        result, meta = await encode_gate.safe_encode_batch(
            texts=["排队中的查询"],
            initial_batch_size=32,
            batch_no=1,
            batch_count=1,
        )

    assert result.shape[0] == 1
    assert meta["device"] == "gpu"
    assert all(entry["result"] != "stall" for entry in meta["retry_chain"])
    assert encode_gate._get_device_cooldown_controller().is_in_cooldown() is False
    after = encode_gate._get_batch_size_controller().get_stats()
    assert after["degrade_total"] == controller_before["degrade_total"]

