"""错误日志根因定位、链路标记与流量隔离/同源聚合防御测试 (P0 级)。

防御目标 (阶段一改造):
- _extract_origin_frame 在单层异常 / 嵌套 raise from / 无 traceback 三种场景下行为正确
- _assign_chain_metadata 同 batch 内同 incident_id 多条只有第一条标 root_cause, chain_seq 严格递增
- 跨 batch 续接: 同 incident_id 后续批次的 chain_seq 续接 DB 中已有最大值 + 1
- 无 incident_id 的旧路径不写 chain_seq / is_root_cause 字段, 兼容已有日志写入路径

防御目标 (流量隔离与同源聚合):
- pytest 用例执行期间产生的日志一律标 regression, 不冒充生产流量
- 同一告警在一个窗口内重复 N 次只落一行, occurrence_count 累加到 N(替代原先"窗口内丢弃")
- 窗口是固定窗口而非滑动窗口: 跨窗口的持续回声会按窗口数分行, 计数不丢
- ERROR 保留首个根因行与其链路标记, 同 incident 回声不新增行; 不同 incident 各自留行
- 聚合开关关闭时退回旧的 WARNING 去重丢弃行为, 保证可回滚
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.log_sink import _extract_origin_frame, DbLogSink  # noqa: E402
from app.core.observability.log_aggregation import (  # noqa: E402
    LogAggregator, build_aggregation_key,
)
from app.core.observability.traffic_class import (  # noqa: E402
    TRAFFIC_CLASS_ADMIN_TEST,
    TRAFFIC_CLASS_PRODUCTION,
    TRAFFIC_CLASS_REGRESSION,
    current_traffic_class,
    resolve_query_traffic_class,
    resolve_traffic_class,
)


# ── _extract_origin_frame: 三个核心场景 ──────────────────────────


def _capture_loguru_exc(callable_):
    """模拟 loguru record["exception"]: 调用一个会抛错的函数, 把结果包成 namedtuple-like 对象。"""
    try:
        callable_()
    except Exception as exc:  # noqa: BLE001
        return SimpleNamespace(type=type(exc), value=exc, traceback=exc.__traceback__)
    raise AssertionError("expected callable to raise")


def test_extract_origin_frame_returns_innermost_for_simple_raise() -> None:
    """单层异常: origin 应为抛出位置(_inner), 而非外层调用点。"""

    def _inner() -> None:
        raise ValueError("boom")

    def _outer() -> None:
        _inner()

    exc = _capture_loguru_exc(_outer)
    module, function, line, exc_type = _extract_origin_frame(exc)

    assert exc_type == "ValueError"
    assert function == "_inner"
    assert line is not None and line > 0
    # 模块名匹配本测试模块
    assert module is not None and module.endswith("test_error_log_root_cause")


def test_extract_origin_frame_handles_raise_from_chain() -> None:
    """嵌套 raise from: traceback 末端帧仍指向最末尾抛出点(_inner_ttl, 即 RuntimeError 抛出位置)。"""

    def _inner_ttl() -> None:
        try:
            raise ValueError("primary")
        except ValueError as primary:
            raise RuntimeError("wrap") from primary

    exc = _capture_loguru_exc(_inner_ttl)
    module, function, line, exc_type = _extract_origin_frame(exc)

    # loguru 的 traceback 链表会沿着 RuntimeError 走到最末端
    assert exc_type == "RuntimeError"
    assert function == "_inner_ttl"
    assert isinstance(line, int) and line > 0
    assert module is not None


def test_extract_origin_frame_returns_all_none_for_missing_exc() -> None:
    """无异常上下文(普通 logger.warning)时, 全返回 None。"""
    module, function, line, exc_type = _extract_origin_frame(None)
    assert (module, function, line, exc_type) == (None, None, None, None)


def test_extract_origin_frame_tolerates_exc_without_traceback() -> None:
    """exc 对象无 traceback 时 (例如 logger.opt 手工构造), 不应抛出, 仅 exc_type 可用。"""
    fake_exc = SimpleNamespace(type=ValueError, value=ValueError("x"), traceback=None)
    module, function, line, exc_type = _extract_origin_frame(fake_exc)
    assert exc_type == "ValueError"
    assert (module, function, line) == (None, None, None)


# ── _assign_chain_metadata: 链路顺序与根因标记 ──────────────────


def _make_session_returning(max_seq: int, has_root: bool):
    """返回一个 mock session, execute 返回 (max_seq, has_root) 的 row。"""
    session = MagicMock()

    def _execute(_sql, _params):
        result = MagicMock()
        result.fetchone.return_value = (max_seq, has_root)
        return result

    session.execute.side_effect = _execute
    return session


def _new_sink_for_test() -> DbLogSink:
    """构造一个 sink 实例但不启动后台 flush 线程, 仅用于纯逻辑测试。"""
    sink = DbLogSink.__new__(DbLogSink)
    return sink


def test_assign_chain_metadata_first_batch_marks_root_cause() -> None:
    """同 incident_id 批内 3 条, 全新链路: 首条标根因, chain_seq 从 0 递增。"""
    sink = _new_sink_for_test()
    session = _make_session_returning(max_seq=-1, has_root=False)
    batch = [
        {"incident_id": "abc1234567890def", "level": "ERROR", "message": "outer"},
        {"incident_id": "abc1234567890def", "level": "ERROR", "message": "middle"},
        {"incident_id": "abc1234567890def", "level": "ERROR", "message": "inner"},
    ]

    sink._assign_chain_metadata(session, batch)

    assert [e["chain_seq"] for e in batch] == [0, 1, 2]
    assert [e["is_root_cause"] for e in batch] == [True, False, False]


def test_assign_chain_metadata_continues_seq_across_batches() -> None:
    """跨 batch 续接: DB 中已有 chain_seq=2 时, 新批次从 3 开始, 不再标 root_cause。"""
    sink = _new_sink_for_test()
    session = _make_session_returning(max_seq=2, has_root=True)
    batch = [
        {"incident_id": "iid_continue_001", "level": "ERROR", "message": "later-1"},
        {"incident_id": "iid_continue_001", "level": "ERROR", "message": "later-2"},
    ]

    sink._assign_chain_metadata(session, batch)

    assert [e["chain_seq"] for e in batch] == [3, 4]
    assert all(e["is_root_cause"] is False for e in batch)


def test_assign_chain_metadata_skips_entries_without_incident_id() -> None:
    """无 incident_id 的 entry 不参与分组, 兼容旧路径(chain_seq/is_root_cause 不应被设置)。"""
    sink = _new_sink_for_test()
    session = _make_session_returning(max_seq=-1, has_root=False)
    batch = [
        {"incident_id": None, "level": "WARNING", "message": "no-incident"},
        {"incident_id": "", "level": "ERROR", "message": "empty-incident"},
    ]

    sink._assign_chain_metadata(session, batch)

    assert "chain_seq" not in batch[0]
    assert "is_root_cause" not in batch[0]
    assert "chain_seq" not in batch[1]
    assert "is_root_cause" not in batch[1]
    # 没有任何分组, 不应触发 SQL 查询
    session.execute.assert_not_called()


def test_assign_chain_metadata_groups_independent_incidents() -> None:
    """同批内多个 incident_id 各自独立计算 root_cause 和 chain_seq。"""
    sink = _new_sink_for_test()
    # 简化: 让所有查询都返回新链路 (max_seq=-1)
    session = _make_session_returning(max_seq=-1, has_root=False)
    batch = [
        {"incident_id": "iid_A_1234567890ab", "level": "ERROR", "message": "A-first"},
        {"incident_id": "iid_B_1234567890ab", "level": "ERROR", "message": "B-first"},
        {"incident_id": "iid_A_1234567890ab", "level": "ERROR", "message": "A-second"},
    ]

    sink._assign_chain_metadata(session, batch)

    # A: 第 0 条根因, 第 2 条续接
    assert batch[0]["chain_seq"] == 0
    assert batch[0]["is_root_cause"] is True
    assert batch[2]["chain_seq"] == 1
    assert batch[2]["is_root_cause"] is False
    # B: 独立计数, 自己批内只有 1 条 -> 0 + 根因
    assert batch[1]["chain_seq"] == 0
    assert batch[1]["is_root_cause"] is True


def test_assign_chain_metadata_does_not_remark_root_when_db_has_root() -> None:
    """DB 已存在 is_root_cause=True 的记录时(异常顺序), 新批次不应再标根因。"""
    sink = _new_sink_for_test()
    # max_seq=-1 + has_root=True 是一种边界: 通常根因写入会让 max_seq>=0
    # 但即使数据状态异常, 也不应因为本批是"看起来空的"而二次标根因
    session = _make_session_returning(max_seq=-1, has_root=True)
    batch = [
        {"incident_id": "iid_X_1234567890ab", "level": "ERROR", "message": "x"},
    ]

    sink._assign_chain_metadata(session, batch)

    assert batch[0]["chain_seq"] == 0
    # 关键断言: max_seq<0 但 has_root=True 时也不再标根因
    assert batch[0]["is_root_cause"] is False


# ──: 流量类别解析 ────────────────────────────────────────


def test_resolve_traffic_class_marks_pytest_process_as_regression() -> None:
    """pytest 进程内未显式标记的流量必须判为 regression, 不得冒充生产流量。"""
    token = current_traffic_class.set(TRAFFIC_CLASS_PRODUCTION)
    try:
        assert resolve_traffic_class() == TRAFFIC_CLASS_REGRESSION
    finally:
        current_traffic_class.reset(token)


def test_resolve_traffic_class_keeps_explicit_admin_test() -> None:
    """入口显式标记(健康探测/设置页测试)优先级高于进程级兜底。"""
    token = current_traffic_class.set(TRAFFIC_CLASS_ADMIN_TEST)
    try:
        assert resolve_traffic_class() == TRAFFIC_CLASS_ADMIN_TEST
    finally:
        current_traffic_class.reset(token)


def test_attempt_recorder_shares_the_same_context_var() -> None:
    """attempt 链路与错误日志必须共用同一个 ContextVar, 否则同一请求会被判成两种流量。"""
    from app.core.ai.attempt_recorder import current_traffic_class as recorder_var

    assert recorder_var is current_traffic_class


def test_resolve_query_traffic_class_defaults_to_production() -> None:
    """查询侧: 不传 = 只看 production; all = 不过滤; 非法值退回 production。"""
    assert resolve_query_traffic_class(None) == TRAFFIC_CLASS_PRODUCTION
    assert resolve_query_traffic_class("all") is None
    assert resolve_query_traffic_class("regression") == TRAFFIC_CLASS_REGRESSION
    assert resolve_query_traffic_class("unknown") == "unknown"
    assert resolve_query_traffic_class("bogus") == TRAFFIC_CLASS_PRODUCTION


# ──: 聚合键与聚合窗口 ────────────────────────────────────


def test_aggregation_key_ignores_incident_for_warning() -> None:
    """WARNING 跨 incident 聚合: 同指纹不同 incident 必须落到同一个键。"""
    base = {"message_fingerprint": "fp1", "traffic_class": "production", "project_id": 7}
    key_a = build_aggregation_key({**base, "level": "WARNING", "incident_id": "iid-a"})
    key_b = build_aggregation_key({**base, "level": "WARNING", "incident_id": "iid-b"})
    assert key_a == key_b


def test_aggregation_key_separates_error_incidents_and_projects() -> None:
    """ERROR 把 incident_id 纳入键, project_id 同样区分, 保住可追溯性。"""
    base = {"message_fingerprint": "fp1", "traffic_class": "production", "level": "ERROR"}
    key_a = build_aggregation_key({**base, "project_id": 7, "incident_id": "iid-a"})
    key_b = build_aggregation_key({**base, "project_id": 7, "incident_id": "iid-b"})
    key_c = build_aggregation_key({**base, "project_id": 8, "incident_id": "iid-a"})
    assert len({key_a, key_b, key_c}) == 3


def test_aggregation_key_is_none_without_fingerprint() -> None:
    """无指纹的日志不参与聚合, 按原样入库。"""
    assert build_aggregation_key({"level": "ERROR", "message_fingerprint": None}) is None


def test_aggregator_counts_echoes_instead_of_dropping_them() -> None:
    """窗口内 100 次同源告警: 只放行 1 条入库, 其余 99 次累加为待写计数。"""
    aggregator = LogAggregator(window_seconds=300.0)
    entry = {
        "message_fingerprint": "fp-orphan",
        "traffic_class": "production",
        "project_id": 11,
        "level": "WARNING",
        "incident_id": None,
    }

    decisions = [aggregator.admit(dict(entry)) for _ in range(100)]
    inserted = [d for d in decisions if d[0] is True]

    assert len(inserted) == 1
    pending = aggregator.take_pending()
    assert list(pending.values()) == [99]
    # 取走后不应重复计数
    assert aggregator.take_pending() == {}


def test_aggregator_reopens_anchor_after_window_expires() -> None:
    """窗口过期后必须重新建行, 否则单行会无限承载历史计数、丢失时间分辨率。"""
    aggregator = LogAggregator(window_seconds=0.0)
    entry = {
        "message_fingerprint": "fp-window",
        "traffic_class": "production",
        "project_id": None,
        "level": "WARNING",
    }
    assert aggregator.admit(dict(entry))[0] is True
    assert aggregator.admit(dict(entry))[0] is True
    assert aggregator.take_pending() == {}


def test_aggregator_uses_fixed_window_not_sliding(monkeypatch) -> None:
    """ 真实场景(前端 1Hz 轮询持续 30 分钟)的落行数必须可预期。

 锚点不随回声续期, 因此 1800 次回声 + 300s 窗口 = 6 行 x 300 次, 合计 1800 次不丢。
 锁定"固定窗口"语义, 防止被误读成"任意时长的重复都只有 1 行"。
 """
    import app.core.observability.log_aggregation as module

    clock = {"t": 0.0}
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: clock["t"]), raising=True,
    )
    aggregator = module.LogAggregator(window_seconds=300.0)
    entry = {
        "message_fingerprint": "fp-orphan-1hz",
        "traffic_class": "production",
        "project_id": 42,
        "level": "WARNING",
    }

    rows, per_row, current = 0, [], 0
    for second in range(1800):
        clock["t"] = float(second)
        should_insert, _ = aggregator.admit(dict(entry))
        if should_insert:
            if rows:
                per_row.append(current)
            rows += 1
            current = 1
        else:
            current += 1
    per_row.append(current)

    assert rows == 6
    assert per_row == [300] * 6
    assert sum(per_row) == 1800


def test_aggregator_restores_pending_on_write_failure() -> None:
    """写库失败时计数必须放回, 不能把发生次数静默吞掉。"""
    aggregator = LogAggregator(window_seconds=300.0)
    aggregator.restore_pending({"k": 3})
    aggregator.restore_pending({"k": 2})
    assert aggregator.take_pending() == {"k": 5}


# ──: DbLogSink 端到端(假 session, 不依赖数据库) ──────────


class _FakeResult:
    def __init__(self, row=None) -> None:
        self._row = row

    def fetchone(self):
        return self._row

    def all(self):
        return []


class _FakeSession:
    """仅覆盖 DbLogSink 用到的 session 接口, 记录写入行与批量累加语句。"""

    def __init__(self, store: dict) -> None:
        self._store = store

    def add(self, obj) -> None:
        self._store["added"].append(obj)

    def flush(self) -> None:
        for obj in self._store["added"]:
            if getattr(obj, "id", None) is None:
                self._store["next_id"] += 1
                obj.id = self._store["next_id"]

    def execute(self, sql, params=None):
        text = str(sql)
        if "UPDATE system_error_logs" in text:
            self._store["updates"].append(params)
            return _FakeResult()
        # _assign_chain_metadata 的链路查询: 恒当作全新链路
        return _FakeResult(row=(-1, False))

    def commit(self) -> None:
        self._store["commits"] += 1

    def rollback(self) -> None:
        self._store["rollbacks"] += 1

    def close(self) -> None:
        pass


def _make_offline_sink(*, aggregation_enabled: bool = True) -> tuple[DbLogSink, dict]:
    """构造一个不会自动后台 flush、写入落到假 session 的 sink。"""
    store = {"added": [], "updates": [], "commits": 0, "rollbacks": 0, "next_id": 0}
    # flush_interval 拉到 1 小时, 避免后台线程与断言竞争同一个 buffer
    sink = DbLogSink(flush_size=10_000, flush_interval=3600.0, dedup_ttl=300.0)
    sink._aggregation_enabled = aggregation_enabled
    sink._sync_session_factory = lambda: _FakeSession(store)
    # 规则刷新同样走假 session, 这里直接跳过, 保持断言聚焦
    sink._last_rule_refresh = 1e12
    return sink, store


def _make_message(
    level_name: str,
    level_no: int,
    message: str,
    *,
    incident_id: str | None = None,
    project_id: int | None = None,
):
    """构造一个最小可用的 loguru message 替身。"""
    extra: dict = {}
    if incident_id is not None:
        extra["incident_id"] = incident_id
    if project_id is not None:
        extra["project_id"] = project_id
    record = {
        "level": SimpleNamespace(no=level_no, name=level_name),
        "name": "app.services.writing.orphan_checker",
        "function": "check_orphans",
        "line": 42,
        "message": message,
        "exception": None,
        "extra": extra,
    }
    return SimpleNamespace(record=record)


def test_sink_marks_pytest_logs_as_regression_traffic() -> None:
    """隔离验证: pytest 进程内写出的日志 traffic_class 必须是 regression。"""
    sink, store = _make_offline_sink()
    sink(_make_message("ERROR", 40, "regression traffic marker"))
    sink._do_flush()

    assert len(store["added"]) == 1
    assert store["added"][0].traffic_class == TRAFFIC_CLASS_REGRESSION


def test_sink_aggregates_repeated_orphan_warning_to_single_row() -> None:
    """ 写标孤儿场景: 同一告警重复 100 次只留一行, 计数累加到 100。"""
    sink, store = _make_offline_sink()
    for _ in range(100):
        sink(_make_message(
            "WARNING", 30, "写标孤儿: 章节未匹配到素材", project_id=11,
        ))
    sink._do_flush()

    assert len(store["added"]) == 1, "同源告警不得每次新增一行"
    anchor = store["added"][0]
    assert anchor.occurrence_count == 1
    assert len(store["updates"]) == 1
    assert store["updates"][0]["deltas"] == [99]
    # 锚点行初始 1 + 回声 99 = 实际发生 100 次
    assert anchor.occurrence_count + store["updates"][0]["deltas"][0] == 100


def test_sink_keeps_error_root_cause_and_folds_echoes() -> None:
    """ERROR 保留首个根因行, 同 incident 回声只累加计数而不新增行。"""
    sink, store = _make_offline_sink()
    for _ in range(5):
        sink(_make_message(
            "ERROR", 40, "章节生成失败: 素材缺失", incident_id="iid_echo_0001",
        ))
    sink._do_flush()

    assert len(store["added"]) == 1
    root = store["added"][0]
    assert root.is_root_cause is True
    assert root.chain_seq == 0
    assert root.incident_id == "iid_echo_0001"
    assert store["updates"][0]["deltas"] == [4]


def test_sink_keeps_separate_rows_for_distinct_incidents() -> None:
    """业务可追踪性: 同一条错误消息属于不同 incident 时必须各自留行。"""
    sink, store = _make_offline_sink()
    sink(_make_message("ERROR", 40, "章节生成失败: 素材缺失", incident_id="iid_a_0001"))
    sink(_make_message("ERROR", 40, "章节生成失败: 素材缺失", incident_id="iid_b_0002"))
    sink._do_flush()

    assert len(store["added"]) == 2
    assert {row.incident_id for row in store["added"]} == {"iid_a_0001", "iid_b_0002"}
    assert store["updates"] == []


def test_sink_keeps_project_id_traceable_on_aggregated_row() -> None:
    """聚合不得抹掉 project_id: 不同项目的同类告警各自留行。"""
    sink, store = _make_offline_sink()
    sink(_make_message("WARNING", 30, "素材缺失", project_id=101))
    sink(_make_message("WARNING", 30, "素材缺失", project_id=202))
    sink._do_flush()

    assert {row.project_id for row in store["added"]} == {101, 202}


def test_sink_falls_back_to_dropping_duplicates_when_aggregation_disabled() -> None:
    """回滚路径: 关闭聚合后退回旧行为(WARNING 窗口内去重丢弃, 不产生累加语句)。"""
    sink, store = _make_offline_sink(aggregation_enabled=False)
    for _ in range(10):
        sink(_make_message("WARNING", 30, "旧行为: 窗口内重复丢弃"))
    sink._do_flush()

    assert len(store["added"]) == 1
    assert store["updates"] == []
