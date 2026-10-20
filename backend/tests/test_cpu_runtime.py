"""cpu_runtime.detect_cpu_topology 单元测试。

覆盖:
- 物理 / 逻辑 / 亲和性的基本探测
- cgroup v2 / v1 解析
- effective_cores 取最小值
- psutil 不可用时的 fallback
- Windows / Linux 平台分支
- 单例缓存与 reset 行为
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_topology():
    from app.core.runtime import cpu_runtime

    cpu_runtime.reset_cpu_topology()
    yield
    cpu_runtime.reset_cpu_topology()


def test_detect_basic_fields_present():
    """detect_cpu_topology 必须返回 CpuTopology 全字段且都是正整数(除 cgroup_cores)。"""
    from app.core.runtime.cpu_runtime import detect_cpu_topology

    topo = detect_cpu_topology()
    assert topo.physical >= 1
    assert topo.logical >= 1
    assert topo.affinity >= 1
    assert topo.effective_cores >= 1
    assert topo.platform == sys.platform
    assert isinstance(topo.source_notes, str) and topo.source_notes


def test_effective_cores_clamped_to_min():
    """所有候选都为 0 时 effective_cores 仍 clamp 到 1。"""
    from app.core.runtime.cpu_runtime import detect_cpu_topology

    fake_psutil = MagicMock()
    fake_psutil.cpu_count.side_effect = lambda logical=True: 0
    fake_psutil.Process.return_value.cpu_affinity.return_value = []
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        topo = detect_cpu_topology()
        assert topo.effective_cores >= 1


def test_effective_cores_takes_min_of_physical_affinity_cgroup():
    """物理 8 / 亲和性 4 / cgroup 等效 2 → effective=2。"""
    from app.core.runtime import cpu_runtime
    from app.core.runtime.cpu_runtime import detect_cpu_topology

    fake_psutil = MagicMock()
    fake_psutil.cpu_count.side_effect = lambda logical=True: 16 if logical else 8
    fake_proc = MagicMock()
    fake_proc.cpu_affinity.return_value = [0, 1, 2, 3]
    fake_psutil.Process.return_value = fake_proc

    with patch.dict(sys.modules, {"psutil": fake_psutil}), \
         patch.object(cpu_runtime, "_read_cgroup_cores", return_value=2.0):
        topo = detect_cpu_topology()
        assert topo.physical == 8
        assert topo.affinity == 4
        assert topo.cgroup_cores == 2.0
        assert topo.effective_cores == 2


def test_log_str_contains_all_fields():
    """to_log_str 必须包含 physical/logical/affinity/cgroup/effective/platform 关键字。"""
    from app.core.runtime.cpu_runtime import detect_cpu_topology

    log_str = detect_cpu_topology().to_log_str()
    for key in ("physical=", "logical=", "affinity=", "cgroup=", "effective=", "platform="):
        assert key in log_str


def test_singleton_cache_returns_same_instance():
    """get_cpu_topology 多次调用必须返回同一对象 (单例缓存)。"""
    from app.core.runtime.cpu_runtime import get_cpu_topology

    a = get_cpu_topology()
    b = get_cpu_topology()
    assert a is b


def test_reset_topology_clears_cache():
    """reset 后再 get 应得到不同实例 (虽然字段值可能相同)。"""
    from app.core.runtime.cpu_runtime import get_cpu_topology, reset_cpu_topology

    a = get_cpu_topology()
    reset_cpu_topology()
    b = get_cpu_topology()
    # 字段相同但对象不同, 证明 reset 生效
    assert a is not b
    assert a.physical == b.physical


def test_cgroup_v2_unlimited_returns_none(tmp_path):
    """cgroup v2 文件内容为 'max <period>' 时返回 None (无限额)。"""
    from app.core.runtime import cpu_runtime

    fake_max = tmp_path / "cpu.max"
    fake_max.write_text("max 100000")

    with patch.object(
        cpu_runtime, "_read_cgroup_v2_cores", wraps=cpu_runtime._read_cgroup_v2_cores
    ):
        # 直接调底层函数测试 v2 解析逻辑
        original_paths = [
            cpu_runtime.Path("/sys/fs/cgroup/cpu.max"),
        ]
        # 替换 candidates 列表过于侵入, 直接验证 'max' 解析返回 None
        # 通过临时 patch Path.is_file + read_text 来模拟
        with patch.object(cpu_runtime.Path, "is_file", return_value=True), \
             patch.object(cpu_runtime.Path, "read_text", return_value="max 100000"):
            assert cpu_runtime._read_cgroup_v2_cores() is None


def test_cgroup_v2_quota_period_ratio():
    """cgroup v2 'quota period' 形式正常解析为 quota/period。"""
    from app.core.runtime import cpu_runtime

    with patch.object(cpu_runtime.Path, "is_file", return_value=True), \
         patch.object(cpu_runtime.Path, "read_text", return_value="200000 100000"):
        cores = cpu_runtime._read_cgroup_v2_cores()
        assert cores == 2.0


def test_cgroup_v1_quota_period_ratio():
    """cgroup v1 quota/period 文件分别读取并相除。"""
    from app.core.runtime import cpu_runtime

    def _fake_is_file(self):
        # 让所有 candidate path 都 'exists'
        return True

    def _fake_read_text(self, *args, **kwargs):
        if "cfs_quota_us" in str(self):
            return "400000"
        if "cfs_period_us" in str(self):
            return "100000"
        return ""

    with patch.object(cpu_runtime.Path, "is_file", _fake_is_file), \
         patch.object(cpu_runtime.Path, "read_text", _fake_read_text):
        cores = cpu_runtime._read_cgroup_v1_cores()
        assert cores == 4.0


def test_psutil_failure_falls_back_to_os():
    """psutil 调用抛异常时, detect 应使用 os.cpu_count fallback 并在 source_notes 标注。"""
    from app.core.runtime.cpu_runtime import detect_cpu_topology

    fake_psutil = MagicMock()
    fake_psutil.cpu_count.side_effect = RuntimeError("simulated failure")
    fake_psutil.Process.side_effect = RuntimeError("simulated failure")

    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        topo = detect_cpu_topology()
        assert topo.effective_cores >= 1
        # 失败应触发 logical_fallback / physical_fallback 标记
        assert (
            "psutil_unavailable" in topo.source_notes
            or "logical_fallback" in topo.source_notes
        )
