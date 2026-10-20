""" 防御测试: 设备解析回退 CPU 时必须区分"装错 torch 包"与"无 GPU"。

映射真实回归:
- 本地 `pip install -r backend/requirements.txt` 让 sentence-transformers 的
 传递依赖从 PyPI 解析 torch, 而 PyPI 的 Windows 轮是 CPU-only 构建, 静默顶掉
 先前用 CUDA 索引装的 torch, 使 `torch.cuda.is_available` 恒为 False。
- 当时日志只有一句"未检测到CUDA，使用CPU", 未点名 torch 版本, 把排查方向
 误导到驱动侧 (nvidia-smi 完全正常)。

断言关注真实输出(解析出的设备 / 日志级别与文案), 而非"能跑通"。
"""

import logging

import pytest
from loguru import logger

from app.core.vectorizer import embedding as embedding_module
from app.core.vectorizer.embedding import EmbeddingModel


@pytest.fixture
def captured_logs(caplog):
    """把 loguru 输出转接到 stdlib logging, 供断言级别与文案。"""
    sink_id = logger.add(
        lambda message: logging.getLogger("embedding_bug840").log(
            logging.getLevelName(message.record["level"].name),
            message.record["message"],
        ),
        level="DEBUG",
    )
    with caplog.at_level(logging.DEBUG, logger="embedding_bug840"):
        yield caplog
    logger.remove(sink_id)


def _resolve(device: str) -> str:
    """绕过 __init__ 副作用, 只驱动设备解析逻辑。"""
    model = EmbeddingModel.__new__(EmbeddingModel)
    return model._resolve_device(device)


def _fake_torch(monkeypatch, *, cuda_available: bool, cuda_build: str | None):
    """伪造 torch 的 CUDA 可用性与构建标记。"""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)
    monkeypatch.setattr(torch.version, "cuda", cuda_build)
    monkeypatch.setattr(torch, "__version__", "2.13.0+cpu" if cuda_build is None else "2.13.0+cu130")


def test_cpu_only_torch_build_warns_and_names_version(monkeypatch, captured_logs):
    """CPU-only 构建 (torch.version.cuda=None) 必须 WARNING 并点名 torch 版本。

    这是本次故障的核心信号: 日志要能一眼区分"装错包"而非驱动问题。
    """
    _fake_torch(monkeypatch, cuda_available=False, cuda_build=None)

    assert _resolve("auto") == "cpu"

    warnings = [r for r in captured_logs.records if r.levelno == logging.WARNING]
    assert warnings, "CPU-only 构建必须升级为 WARNING"
    message = warnings[-1].message
    assert "CPU-only" in message, message
    assert "2.13.0+cpu" in message, message
    assert "驱动" in message, message


def test_real_no_gpu_environment_stays_info(monkeypatch, captured_logs):
    """反向防御: torch 含 CUDA 支持但环境无 GPU 时, auto 回退 CPU 属预期内。

 纯 CPU 部署是合法形态, 不得因本次修复把它误报成 WARNING (教训)。
 """
    _fake_torch(monkeypatch, cuda_available=False, cuda_build="13.0")

    assert _resolve("auto") == "cpu"

    warnings = [r for r in captured_logs.records if r.levelno == logging.WARNING]
    assert not warnings, [r.message for r in warnings]
    infos = [r.message for r in captured_logs.records if r.levelno == logging.INFO]
    assert any("2.13.0+cu130" in m for m in infos), infos


def test_explicit_cuda_without_gpu_warns_about_driver(monkeypatch, captured_logs):
    """显式配置 cuda 却无 GPU, 且 torch 本身支持 CUDA → 指向驱动侧排查。"""
    _fake_torch(monkeypatch, cuda_available=False, cuda_build="13.0")

    assert _resolve("cuda") == "cpu"

    warnings = [r for r in captured_logs.records if r.levelno == logging.WARNING]
    assert warnings, "显式要求 CUDA 却拿不到 GPU 必须 WARNING"
    assert "驱动" in warnings[-1].message, warnings[-1].message


def test_explicit_cuda_on_cpu_only_build_points_at_torch(monkeypatch, captured_logs):
    """显式 cuda + CPU-only 构建时, 诊断必须指向 torch 包而非驱动。"""
    _fake_torch(monkeypatch, cuda_available=False, cuda_build=None)

    assert _resolve("cuda") == "cpu"

    warnings = [r for r in captured_logs.records if r.levelno == logging.WARNING]
    assert warnings
    assert "CPU-only" in warnings[-1].message, warnings[-1].message


def test_auto_resolves_cuda_when_available(monkeypatch, captured_logs):
    """CUDA 正常可用时 auto 必须解析为 cuda, 且不产生回退告警。"""
    _fake_torch(monkeypatch, cuda_available=True, cuda_build="13.0")

    assert _resolve("auto") == "cuda"

    warnings = [r for r in captured_logs.records if r.levelno == logging.WARNING]
    assert not warnings, [r.message for r in warnings]


def test_explicit_cpu_config_skips_cuda_diagnosis(monkeypatch, captured_logs):
    """显式 cpu 是部署层主动选择, 不应触发任何 CUDA 回退诊断日志。"""
    _fake_torch(monkeypatch, cuda_available=False, cuda_build=None)

    assert _resolve("cpu") == "cpu"

    messages = [r.message for r in captured_logs.records]
    assert not any("CPU-only" in m for m in messages), messages


def test_log_helper_is_used_by_resolver(monkeypatch):
    """锁定 _resolve_device 走统一诊断入口, 防止未来绕过护栏直接打日志。"""
    _fake_torch(monkeypatch, cuda_available=False, cuda_build=None)
    calls: list[str] = []
    monkeypatch.setattr(
        embedding_module, "_log_cuda_unavailable", lambda configured: calls.append(configured)
    )

    assert _resolve("auto") == "cpu"

    assert calls == ["auto"]
