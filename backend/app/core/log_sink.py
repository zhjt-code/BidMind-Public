"""Loguru DB Sink -- 将 WARNING+ 日志同步写入 system_error_logs 表

设计要点:
- Loguru sink 在同步上下文中被调用,使用后台 daemon 线程执行 DB 写入
- 使用独立的同步 SQLAlchemy engine(psycopg2),避免与主事件循环的 asyncpg 连接池冲突
- 内存缓冲 + 定时/定量批量 flush,避免逐条写入
- 每条日志按当前流量类别(production / admin_test / regression)入库,测试与探测流量可被隔离
- 同源日志按指纹窗口聚合: 窗口内首条落库为锚点,后续回声只累加 occurrence_count
- 聚合关闭时退回旧行为(WARNING 窗口内去重丢弃, ERROR / CRITICAL 全量入库)
- 管理员可创建抑制规则(log_suppression_rules),匹配的日志不入 DB 但文件 sink 不受影响
- 写入失败降级到 stderr,绝不阻塞业务
"""

import sys
import time
import threading
import traceback as tb_module
from typing import Optional

from sqlalchemy import text as sa_text

from app.models.system_error_log import SystemErrorLog
from app.models.log_suppression_rule import LogSuppressionRule
from app.core.message_fingerprint import compute_fingerprint
from app.core.observability.log_aggregation import LogAggregator
from app.core.observability.traffic_class import resolve_traffic_class


def _extract_origin_frame(exc) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    """从 loguru 的 record["exception"] 中提取 traceback 末端帧坐标与异常类型。

    末端帧 = traceback 链表上 tb_next is None 的那一帧, 也就是真正抛出异常的代码位置。
    区别于调用 logger.error 的位置, 这才是定位根因需要的"原点"。

    Args:
        exc: loguru 的 ExceptionInfo 对象 (record["exception"]), 形如
             namedtuple(type, value, traceback)。可能为 None。

    Returns:
        (origin_module, origin_function, origin_line, exception_type) 四元组,
        任一无法取得时对应位置返回 None。永不抛出。
    """
    if exc is None:
        return None, None, None, None
    try:
        exc_type = exc.type.__name__ if exc.type is not None else None
    except Exception:
        exc_type = None

    tb = getattr(exc, "traceback", None)
    if tb is None:
        return None, None, None, exc_type

    try:
        while tb.tb_next is not None:
            tb = tb.tb_next
        frame = tb.tb_frame
        module_name = frame.f_globals.get("__name__")
        function_name = frame.f_code.co_name
        line_no = tb.tb_lineno
        return (
            module_name if isinstance(module_name, str) else None,
            function_name if isinstance(function_name, str) else None,
            int(line_no) if line_no is not None else None,
            exc_type,
        )
    except Exception:
        return None, None, None, exc_type

TASK_CATEGORY_MAP: dict[str, str] = {
    "process": "bid_review",
    "extract": "bid_review",
    "review": "bid_review",
    "collusion": "collusion_check",
    "writing_extract": "bid_writing",
    "writing_clause_vectorize": "bid_writing",
    "writing_outline": "bid_writing",
    "writing_match": "bid_writing",
    "writing_compose": "bid_writing",
}


def _aggregation_enabled_by_config() -> bool:
    """读取聚合开关; settings 尚未就绪时保守启用(与默认配置一致)"""
    try:
        from app.core.config import settings
        return bool(getattr(settings, "ERROR_LOG_AGGREGATION_ENABLED", True))
    except Exception:
        return True


def _create_sync_session_factory():
    """创建独立的同步 session 工厂,仅供 log sink 使用"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2", 1)
    engine = create_engine(
        sync_url,
        pool_size=2,
        max_overflow=3,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


class DbLogSink:
    """线程安全的数据库日志 Sink"""

    def __init__(
        self,
        flush_size: int = 50,
        flush_interval: float = 5.0,
        dedup_ttl: float = 300.0,
        buffer_maxlen: int = 1000,
        rule_refresh_interval: float = 60.0,
    ) -> None:
        self._flush_size = flush_size
        self._flush_interval = flush_interval
        self._dedup_ttl = dedup_ttl
        self._buffer_maxlen = buffer_maxlen

        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._dedup_cache: dict[str, float] = {}

        self._aggregation_enabled = _aggregation_enabled_by_config()
        self._aggregator = LogAggregator(window_seconds=dedup_ttl)

        self._sync_session_factory = None
        self._factory_lock = threading.Lock()

        # 抑制规则: 内存缓存 + 计数器
        self._suppression_rules: list[dict] = []
        self._suppression_counters: dict[int, int] = {}
        self._last_rule_refresh: float = 0.0
        self._rule_refresh_interval = rule_refresh_interval

        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def _get_session_factory(self):
        """延迟初始化同步 session 工厂(首次 flush 时创建)"""
        if self._sync_session_factory is None:
            with self._factory_lock:
                if self._sync_session_factory is None:
                    self._sync_session_factory = _create_sync_session_factory()
        return self._sync_session_factory

    # ── Loguru sink 入口 ─────────────────────────────────────────

    def __call__(self, message) -> None:
        record = message.record
        level_no = record["level"].no

        if level_no < 30:
            return

        # 聚合关闭时(回滚路径)退回旧的 WARNING 窗口去重
        if not self._aggregation_enabled and level_no == 30:
            if self._is_repeated_warning(record):
                return

        # 抑制规则检查（纯内存匹配，不阻塞）
        matched_rule_id = self._match_suppression_rule(
            module=record["name"],
            function_name=record["function"],
            message=str(record["message"]),
            level=record["level"].name,
        )
        if matched_rule_id is not None:
            with self._lock:
                self._suppression_counters[matched_rule_id] = (
                    self._suppression_counters.get(matched_rule_id, 0) + 1
                )
            return

        # 提取 traceback 文本
        exc = record["exception"]
        traceback_text: Optional[str] = None
        if exc is not None:
            try:
                traceback_text = "".join(
                    tb_module.format_exception(exc.type, exc.value, exc.traceback)
                )
            except Exception:
                traceback_text = str(exc)

        extra = record["extra"]
        task_type = extra.get("task_type")
        category = (
            extra.get("category")
            or TASK_CATEGORY_MAP.get(task_type, "system")  # type: ignore[arg-type]
        )
        level_name = record["level"].name
        message_text = str(record["message"])[:4000]
        module_name = record["name"]

        # 解析 traceback 末端帧, 用于定位真正的异常发生点 (与 logger.error 调用点区分)
        origin_module, origin_function, origin_line, exception_type = _extract_origin_frame(exc)

        # 链路标识: 由 middleware / 任务入口 contextualize 注入, 旧路径未注入时为空
        incident_id_raw = extra.get("incident_id")
        incident_id = (
            str(incident_id_raw)[:32] if incident_id_raw else None
        )

        entry = {
            "level": level_name,
            "message": message_text,
            "traceback": traceback_text,
            "module": module_name,
            "function_name": record["function"],
            "line_number": record["line"],
            "request_id": extra.get("request_id"),
            "user_id": extra.get("user_id"),
            "project_id": extra.get("project_id"),
            "category": category,
            "task_type": task_type,
            "stage": extra.get("stage"),
            "source": extra.get("source", "backend"),
            "traffic_class": resolve_traffic_class(),
            "occurrence_count": 1,
            "message_fingerprint": compute_fingerprint(module_name, level_name, message_text),
            "incident_id": incident_id,
            "exception_type": exception_type,
            "origin_module": origin_module,
            "origin_function": origin_function,
            "origin_line": origin_line,
            # chain_seq / is_root_cause 在 _write_batch_sync 中按 incident_id 分组赋值
        }

        # 同源回声不新增行, 只累加计数(计数在 flush 时批量落库)
        if self._aggregation_enabled:
            should_insert, agg_key = self._aggregator.admit(entry)
            if not should_insert:
                return
            if agg_key is not None:
                entry["_agg_key"] = agg_key

        with self._lock:
            if len(self._buffer) < self._buffer_maxlen:
                self._buffer.append(entry)
            need_flush = len(self._buffer) >= self._flush_size

        if need_flush:
            self._do_flush()

    def _is_repeated_warning(self, record) -> bool:
        """旧版 WARNING 去重: 窗口内同 module:function:message 前缀直接丢弃"""
        dedup_key = (
            f"{record['name']}:{record['function']}:"
            f"{str(record['message'])[:50]}"
        )
        now = time.time()
        with self._lock:
            last = self._dedup_cache.get(dedup_key)
            if last is not None and (now - last) < self._dedup_ttl:
                return True
            self._dedup_cache[dedup_key] = now
        return False

    # ── 后台定时 flush ───────────────────────────────────────────

    def _flush_loop(self) -> None:
        while True:
            time.sleep(self._flush_interval)
            self._do_flush()

    def _do_flush(self) -> None:
        with self._lock:
            batch = self._buffer[:]
            self._buffer.clear()
            self._cleanup_dedup_cache()
            counters = self._suppression_counters.copy()
            self._suppression_counters.clear()

        pending = self._aggregator.take_pending() if self._aggregation_enabled else {}
        # 失败回滚需要用到锚点键, 而 _write_batch_sync 会把它从 entry 上摘掉, 故先留存
        batch_keys = [e["_agg_key"] for e in batch if e.get("_agg_key")]

        if batch or pending:
            try:
                self._write_batch_sync(batch, pending)
            except Exception as exc:
                # 计数放回、锚点作废: 让下一条同源日志重建行, 而不是把发生次数吞掉
                self._aggregator.restore_pending(pending)
                self._aggregator.discard_anchors(batch_keys)
                sys.stderr.write(f"[DbLogSink] flush failed ({len(batch)} entries): {exc}\n")

        if counters:
            try:
                self._flush_suppression_counters(counters)
            except Exception as exc:
                sys.stderr.write(f"[DbLogSink] suppression counter flush failed: {exc}\n")

        self._maybe_refresh_rules()

    def _cleanup_dedup_cache(self) -> None:
        """清理过期去重 key(在 _lock 内调用)"""
        now = time.time()
        expired = [k for k, ts in self._dedup_cache.items() if now - ts > self._dedup_ttl]
        for k in expired:
            del self._dedup_cache[k]

    # ── 抑制规则 ──────────────────────────────────────────────────

    def _match_suppression_rule(
        self, *, module: str, function_name: str, message: str, level: str,
    ) -> Optional[int]:
        """在内存规则列表中查找匹配项，返回 rule_id 或 None"""
        for rule in self._suppression_rules:
            if rule["module"] != module:
                continue
            if rule.get("function_name") and rule["function_name"] != function_name:
                continue
            if rule.get("level") and rule["level"] != level:
                continue
            if not message.startswith(rule["message_prefix"]):
                continue
            return rule["id"]
        return None

    def _maybe_refresh_rules(self) -> None:
        """周期性从 DB 加载 enabled 抑制规则到内存，失败静默跳过"""
        now = time.time()
        if (now - self._last_rule_refresh) < self._rule_refresh_interval:
            return
        self._last_rule_refresh = now
        try:
            self._load_suppression_rules()
        except Exception as exc:
            sys.stderr.write(f"[DbLogSink] rule refresh failed: {exc}\n")

    def _load_suppression_rules(self) -> None:
        """同步加载全部启用中的抑制规则"""
        SessionLocal = self._get_session_factory()
        session = SessionLocal()
        try:
            from sqlalchemy import select
            stmt = select(
                LogSuppressionRule.id,
                LogSuppressionRule.module,
                LogSuppressionRule.function_name,
                LogSuppressionRule.message_prefix,
                LogSuppressionRule.level,
            ).where(LogSuppressionRule.enabled == True)  # noqa: E712
            rows = session.execute(stmt).all()
            self._suppression_rules = [
                {
                    "id": r[0],
                    "module": r[1],
                    "function_name": r[2],
                    "message_prefix": r[3],
                    "level": r[4],
                }
                for r in rows
            ]
        finally:
            session.close()

    def _flush_suppression_counters(self, counters: dict[int, int]) -> None:
        """批量回写抑制计数器增量"""
        SessionLocal = self._get_session_factory()
        session = SessionLocal()
        try:
            for rule_id, delta in counters.items():
                session.execute(
                    sa_text(
                        "UPDATE log_suppression_rules "
                        "SET suppressed_count = suppressed_count + :delta, "
                        "    last_suppressed_at = NOW() "
                        "WHERE id = :rule_id"
                    ),
                    {"delta": delta, "rule_id": rule_id},
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── 同步批量写入 ─────────────────────────────────────────────

    def _assign_chain_metadata(self, session, batch: list[dict]) -> None:
        """为本批日志按 incident_id 分组分配 chain_seq 与 is_root_cause。

        约定:
        - 每条日志写入前确认 entry["incident_id"] 是否已知; 未知时全 NULL/False, 退化为旧路径
        - 同 incident_id 的多条日志: 按当前 batch 顺序（_buffer.append 顺序近似时间序）
          续接 DB 中已有的最大 chain_seq + 1, +2 ...
        - 仅当 DB 中既无该 incident_id 也无任何 is_root_cause=True 时, 把本批最早一条标根因
        - 单条 SELECT/incident_id, 默认 5s flush 单批 ≤50 条, 增量负载可接受
        - 失败静默, 不影响主流程写入

        Args:
            session: 同步 SQLAlchemy session, 由调用方保持事务
            batch: 待写入 entry 字典列表 (in-place 修改)
        """
        groups: dict[str, list[dict]] = {}
        for entry in batch:
            iid = entry.get("incident_id")
            if not iid:
                continue
            groups.setdefault(iid, []).append(entry)

        for iid, entries in groups.items():
            row = session.execute(
                sa_text(
                    "SELECT COALESCE(MAX(chain_seq), -1) AS max_seq, "
                    "BOOL_OR(is_root_cause) AS has_root "
                    "FROM system_error_logs WHERE incident_id = :iid"
                ),
                {"iid": iid},
            ).fetchone()
            max_seq = -1
            has_root = False
            if row is not None:
                raw_max = row[0] if row[0] is not None else -1
                try:
                    max_seq = int(raw_max)
                except (TypeError, ValueError):
                    max_seq = -1
                has_root = bool(row[1]) if row[1] is not None else False

            for offset, entry in enumerate(entries, start=1):
                entry["chain_seq"] = max_seq + offset
                # 仅当 DB 中既无任何记录(max_seq < 0)又无 root_cause 标记时, 把本批最早一条标根因
                entry["is_root_cause"] = (offset == 1 and not has_root and max_seq < 0)

    def _apply_occurrence_deltas(
        self, session, pending: dict[str, int], fresh_anchors: dict[str, int],
    ) -> None:
        """把回声计数一次性累加到各自锚点行。

        用单条 UPDATE ... FROM unnest 完成全部累加, 避免逐条 UPDATE 的 N+1;
        锚点尚未落库的键退回待写队列, 下一批再累加。
        """
        updates: list[tuple[int, int]] = []
        leftover: dict[str, int] = {}
        for agg_key, delta in pending.items():
            row_id = fresh_anchors.get(agg_key) or self._aggregator.anchor_id(agg_key)
            if row_id is None:
                leftover[agg_key] = delta
                continue
            updates.append((row_id, delta))

        self._aggregator.restore_pending(leftover)
        if not updates:
            return

        session.execute(
            sa_text(
                "UPDATE system_error_logs AS t "
                "SET occurrence_count = t.occurrence_count + v.delta, "
                "    updated_at = NOW() "
                "FROM (SELECT unnest(CAST(:ids AS integer[])) AS id, "
                "             unnest(CAST(:deltas AS integer[])) AS delta) AS v "
                "WHERE t.id = v.id"
            ),
            {"ids": [u[0] for u in updates], "deltas": [u[1] for u in updates]},
        )

    def _write_batch_sync(
        self, batch: list[dict], pending: Optional[dict[str, int]] = None,
    ) -> None:
        SessionLocal = self._get_session_factory()
        session = SessionLocal()
        fresh_anchors: dict[str, int] = {}
        try:
            # 在写入前为带 incident_id 的日志分配链路顺序与根因标记
            self._assign_chain_metadata(session, batch)
            anchor_rows: list[tuple[str, SystemErrorLog]] = []
            for entry in batch:
                agg_key = entry.pop("_agg_key", None)
                # is_root_cause 默认值由模型保证 False; chain_seq 缺省为 NULL
                row = SystemErrorLog(**entry)
                session.add(row)
                if agg_key is not None:
                    anchor_rows.append((agg_key, row))

            # flush 先取回自增 id, 让本批新建的锚点也能接住同批产生的回声计数
            session.flush()
            fresh_anchors.update({key: int(row.id) for key, row in anchor_rows})
            if pending:
                self._apply_occurrence_deltas(session, pending, fresh_anchors)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        for agg_key, row_id in fresh_anchors.items():
            self._aggregator.bind_anchor(agg_key, row_id)


# 模块级单例,供 main.py / worker.py 共用
db_log_sink = DbLogSink()
