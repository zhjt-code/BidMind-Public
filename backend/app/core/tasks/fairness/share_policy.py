"""项目份额裁决策略

职责: 由一份占用快照纯计算出"本 job 能否占用所属项目的份额"
不做: 不访问 Redis/DB、不抛异常做流程控制、不延迟重排 job、不管 LLM 请求级并发

为什么单独成模块:
 份额裁决是本次修复里唯一有真实算法的部分（max-min 水位填充、cohort 排队、让路窗口、
 双时间残留判据）, 也是最容易出错的部分。把它做成不碰 I/O 的纯函数, 防御测试才能直接
 针对算法本身构造边界场景, 而不必先搭一套 Redis 替身。

三条判据（沿用 在 resource_permit 上验证过的思路）:
 1. 持有者以"心跳是否过期"判定死活 —— 持有期间会主动续期, 心跳过期即持有进程已死。
 2. 等待者以"是否还在重新轮询"判定死活 —— 已取消的 job 不会再回来, 超时即清掉,
 否则它会永久占着排队位并虚高本项目的需求量。
 3. 绝不用"某个集合为空"当作残留判据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

_FIELD_SEPARATOR = "|"


@dataclass(frozen=True)
class AdmissionSnapshot:
    """某个资源类型下的占用快照。"""

    holders: Mapping[str, str]
    waiting: Mapping[str, str]


@dataclass(frozen=True)
class ShareRequest:
    """一次份额申请的全部入参。"""

    project: str
    field: str
    capacity: int
    target_projects: int
    now: int
    holder_ttl: int
    waiter_ttl: int
    probation: float


@dataclass(frozen=True)
class _Tally:
    """从快照统计出的在途与排队现状, 供裁决直接使用。"""

    running: Mapping[str, int]
    first_wait: Mapping[str, int]
    my_running: int
    total_running: int
    cohort_size: int
    my_first_wait: int


@dataclass(frozen=True)
class AdmissionOutcome:
    """裁决结果, 以及本次需要落盘的清理动作。"""

    granted: bool
    allow: int
    project_running: int
    total_running: int
    cohort_size: int
    reason: str
    stale_holders: tuple[str, ...]
    stale_waiters: tuple[str, ...]
    waiting_value: str


def split_field(field: str) -> tuple[str, str]:
    """字段形如 "{project}|{owner}"; 缺少分隔符时整体视为项目段。"""

    project, sep, owner = field.partition(_FIELD_SEPARATOR)
    return (project, owner) if sep else (field, "")


def _to_int(value: object) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_waiting(value: object) -> tuple[Optional[int], Optional[int]]:
    """解析排队登记值 "{首次排队时间}:{最近一次轮询时间}"。"""

    text = str(value).strip()
    first, sep, seen = text.partition(":")
    if not sep:
        return None, None
    return _to_int(first), _to_int(seen)


def max_min_allocation(demand: Mapping[str, int], capacity: int) -> dict[str, int]:
    """max-min 水位填充: 需求少的项目先被满足, 剩余容量再均分给仍有需求的项目。

    这是工作保持性的来源 —— 只有一个项目有需求时它拿到全部容量, 有人退出时它腾出的
    份额自动流向还想要的项目, 不会有容量因为"公平"而闲置。
    """

    allocation: dict[str, int] = {}
    remaining = max(int(capacity), 0)
    pending = [key for key, want in demand.items() if want > 0]

    while pending and remaining > 0:
        fair = remaining // len(pending)
        if fair < 1:
            break
        rest: list[str] = []
        progressed = False
        for key in pending:
            want = int(demand[key])
            if want <= fair:
                allocation[key] = want
                remaining -= want
                progressed = True
            else:
                rest.append(key)
        if not progressed:
            # 所有剩余项目的需求都超过均分值, 直接按均分值封顶
            for key in rest:
                allocation[key] = fair
            break
        pending = rest
    return allocation


def _live_holders(
    snapshot: AdmissionSnapshot, request: ShareRequest
) -> tuple[dict[str, int], list[str]]:
    """区分仍在跑的持有者与心跳过期的残留。"""

    running: dict[str, int] = {}
    stale: list[str] = []
    for field, raw in snapshot.holders.items():
        heartbeat = _to_int(raw)
        # 时间戳不可解析说明这条记录已无法判定死活; 留着它会让份额永久少一格且难以察觉,
        # 清掉最坏只是短暂超发一个名额, 下一轮裁决即自愈, 故选择清理。
        if heartbeat is None or request.now - heartbeat > request.holder_ttl:
            stale.append(field)
            continue
        project, _ = split_field(field)
        running[project] = running.get(project, 0) + 1
    return running, stale


def _live_waiters(
    snapshot: AdmissionSnapshot, request: ShareRequest
) -> tuple[dict[str, int], dict[str, int], list[str], Optional[int]]:
    """区分仍在排队的等待者与已不再轮询的残留, 并取出本 job 的首次排队时间。"""

    waiting: dict[str, int] = {}
    first_wait: dict[str, int] = {}
    stale: list[str] = []
    my_first_wait: Optional[int] = None

    for field, raw in snapshot.waiting.items():
        first, seen = _parse_waiting(raw)
        if first is None or seen is None or request.now - seen > request.waiter_ttl:
            stale.append(field)
            continue
        project, _ = split_field(field)
        waiting[project] = waiting.get(project, 0) + 1
        if project not in first_wait or first < first_wait[project]:
            first_wait[project] = first
        if field == request.field:
            my_first_wait = first
    return waiting, first_wait, stale, my_first_wait


def evaluate_admission(
    snapshot: AdmissionSnapshot, request: ShareRequest
) -> AdmissionOutcome:
    """裁决本次申请, 并给出需要顺带清理的残留字段。"""

    running, stale_holders = _live_holders(snapshot, request)
    waiting, first_wait, stale_waiters, my_first_wait = _live_waiters(snapshot, request)

    demand: dict[str, int] = dict(running)
    for project, count in waiting.items():
        demand[project] = demand.get(project, 0) + count
    if my_first_wait is None:
        # 首次申请时本 job 还没登记到排队表, 需求里必须补上自己这一份
        demand[request.project] = demand.get(request.project, 0) + 1

    allocation = max_min_allocation(demand, request.capacity)
    base = max(request.capacity // max(request.target_projects, 1), 1)
    allow = base
    if (
        my_first_wait is not None
        and request.now - my_first_wait >= request.probation
    ):
        # 让路窗口已过: 此刻别的项目若有需求, 早就该被 claim 并登记进来了,
        # 因此现在按 max-min 借用空闲份额是安全的 (工作保持)。
        allow = max(base, allocation.get(request.project, 0))

    tally = _Tally(
        running=running,
        first_wait=first_wait,
        my_running=running.get(request.project, 0),
        total_running=sum(running.values()),
        cohort_size=len(running),
        my_first_wait=my_first_wait if my_first_wait is not None else request.now,
    )
    granted, reason = _decide(request, tally, allow)

    return AdmissionOutcome(
        granted=granted,
        allow=allow,
        project_running=tally.my_running,
        total_running=tally.total_running,
        cohort_size=tally.cohort_size,
        reason=reason,
        stale_holders=tuple(stale_holders),
        stale_waiters=tuple(stale_waiters),
        waiting_value=f"{tally.my_first_wait}:{request.now}",
    )


def _decide(request: ShareRequest, tally: _Tally, allow: int) -> tuple[bool, str]:
    """按"份额 -> 总容量 -> cohort 名额 -> 排队资历"的顺序逐层放行。"""

    if tally.my_running >= allow:
        return False, "over_share"
    if tally.total_running >= request.capacity:
        return False, "capacity_full"
    if tally.my_running > 0:
        # 已在 cohort 内, 份额还有余量, 无需再过名额与排队检查
        return True, "granted"

    free_slots = request.target_projects - tally.cohort_size
    if free_slots <= 0:
        return False, "cohort_full"

    older = sum(
        1
        for project, first in tally.first_wait.items()
        if project != request.project
        and tally.running.get(project, 0) == 0
        and first < tally.my_first_wait
    )
    if older >= free_slots:
        # 排在更早等待者后面: 名额有限时按等待时长排队, 这是防饥饿的关键
        return False, "queued_behind_older"
    return True, "granted"
