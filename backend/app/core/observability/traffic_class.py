"""流量类别上下文 -- 全项目区分生产流量与测试/探测流量的唯一权威定义。

职责: 以 contextvar 维护当前调用链的流量类别, 并给出未显式标记时的进程级默认值
不做: 不落库、不做统计聚合、不感知具体观测通道(LLM attempt 事实表与错误日志各自消费)

历史: 该 contextvar 原定义在 app/core/ai/attempt_recorder.py, 只服务 LLM attempt 链路。
错误日志接入同一语义后必须共用同一个 ContextVar 对象, 否则同一次请求会在两套
观测里被判成不同流量类别, 因此上移到本模块, attempt_recorder 改为再导出。
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Optional

TRAFFIC_CLASS_PRODUCTION = "production"
TRAFFIC_CLASS_ADMIN_TEST = "admin_test"
TRAFFIC_CLASS_REGRESSION = "regression"
# unknown 仅用于存量数据: 加列迁移之前写入的历史行无法回溯真实来源, 禁止伪造回填
TRAFFIC_CLASS_UNKNOWN = "unknown"

KNOWN_TRAFFIC_CLASSES: tuple[str, ...] = (
    TRAFFIC_CLASS_PRODUCTION,
    TRAFFIC_CLASS_ADMIN_TEST,
    TRAFFIC_CLASS_REGRESSION,
    TRAFFIC_CLASS_UNKNOWN,
)

# 查询侧显式要求"不过滤"的取值
TRAFFIC_CLASS_ALL = "all"

# contextvar 名保留 llm_ 前缀之外的中性命名; 默认 production, 与既有 attempt 链路语义一致
current_traffic_class: ContextVar[str] = ContextVar(
    "bidmind_traffic_class", default=TRAFFIC_CLASS_PRODUCTION
)


def _is_regression_process() -> bool:
    """判断当前是否正处在 pytest 用例执行中。

    为什么需要进程级兜底: contextvar 不跨线程传播, 而 TestClient 把请求跑在 anyio
    portal 线程里, 应用内部产生的日志读不到 conftest 在主线程设置的值。
    PYTEST_CURRENT_TEST 是进程级环境变量, 进程内所有线程共享, 能覆盖该场景。

    为什么只认环境变量、不认 "pytest" in sys.modules: 后者的失效方向是危险的 --
    只要任何一个依赖在 import 期碰了 pytest, 生产进程就会把全部真实故障标成
    regression, 再被默认 production 过滤器挡掉, 运维直接看不到故障。误判必须
    倒向"多显示噪声", 绝不能倒向"隐藏生产故障"。
    """
    return "PYTEST_CURRENT_TEST" in os.environ


def resolve_traffic_class() -> str:
    """解析当前流量类别: 入口显式标记优先, 未标记时回落到进程级默认。

    production 与"未标记"在 contextvar 上不可区分(默认值即 production),
    这里按"未标记"处理: 在 pytest 进程内一律判为 regression。
    """
    value = current_traffic_class.get()
    if value and value != TRAFFIC_CLASS_PRODUCTION:
        return value
    if _is_regression_process():
        return TRAFFIC_CLASS_REGRESSION
    return TRAFFIC_CLASS_PRODUCTION


def resolve_query_traffic_class(value: Optional[str]) -> Optional[str]:
    """把查询侧入参归一为"要过滤的类别", 返回 None 表示不加流量过滤。

    - None(调用方未指定) -> production, 即默认只看生产流量
    - "all" -> None, 显式要求跨全部类别
    - 已知类别 -> 原值
    - 非法值 -> 退回 production, 避免拼错参数意外放开噪声
    """
    if value is None:
        return TRAFFIC_CLASS_PRODUCTION
    normalized = value.strip().lower()
    if normalized == TRAFFIC_CLASS_ALL:
        return None
    if normalized in KNOWN_TRAFFIC_CLASSES:
        return normalized
    return TRAFFIC_CLASS_PRODUCTION
