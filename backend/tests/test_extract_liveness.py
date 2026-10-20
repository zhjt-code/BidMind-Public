"""招标要求提取失活判定防御测试。

职责: 锁定「任务已死」谓词——执行体不在且工作未完成才允许宣告死亡。
不做: 不启动 ARQ/worker、不跑真实提取、不测 HTTP 响应组装。

问题形态: 长阶段心跳过期后，状态接口既不完成也不失败；判死必须用
inspect_task_liveness 的 stale/missing，不能用落库条数或单独墙钟。
夹具 1: mandatory_coverage（项目 1528）
夹具 2: scoring_extract（同形态不同阶段）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks import TaskLiveness  # noqa: E402
from app.models.project import ProjectStatus  # noqa: E402
from app.services.liveness.extract_liveness import (  # noqa: E402
    EXTRACT_ARQ_FUNCTION,
    EXTRACT_TERMINAL_STAGES,
    extract_heartbeat_is_midstage,
    inspect_extract_task_liveness,
    should_declare_extract_dead,
)


def _liveness(
    *,
    state: str,
    stage: str = "mandatory_coverage",
    reason: str = "heartbeat_stale",
    heartbeat: dict | None = None,
    age_seconds: float | None = 700.0,
) -> TaskLiveness:
    hb = heartbeat
    if hb is None and state != "missing":
        hb = {"stage": stage, "progress": "52%|核验否决与资格条款"}
    return TaskLiveness(
        task_name="extract_project_1528",
        state=state,
        active=state in {"live", "enqueue_grace"},
        heartbeat=hb,
        stage=stage if state != "missing" else "",
        age_seconds=age_seconds,
        reason=reason,
    )


def test_extract_arq_function_and_chinese_failed_are_terminals():
    """提取 job 名与中文失败心跳必须纳入终态集合，否则会把「失败」当中途静默。"""
    assert EXTRACT_ARQ_FUNCTION == "extract_requirements_job"
    assert "失败" in EXTRACT_TERMINAL_STAGES
    assert "completed" in EXTRACT_TERMINAL_STAGES
    assert "failed" in EXTRACT_TERMINAL_STAGES


@pytest.mark.parametrize(
    "stage",
    ["mandatory_coverage", "scoring_extract"],
)
def test_stale_extracting_midstage_is_dead(stage: str):
    """EXTRACTING + stale + 中间阶段 → 已死。两阶段同一谓词，禁止只认核验。"""
    liveness = _liveness(state="stale", stage=stage, heartbeat={"stage": stage})
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is True


def test_stale_uploading_midstage_is_dead_not_idle():
    """未达业务终态的中途静默不得回 idle（自动重启环）。"""
    liveness = _liveness(
        state="stale",
        stage="scoring_extract",
        heartbeat={"stage": "scoring_extract", "progress": "88%|评分项提取中"},
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.UPLOADING) is True


def test_arq_pending_live_is_not_dead():
    """队列侧确证在途时心跳过期仍视为活，不得写 FAILED。"""
    liveness = _liveness(
        state="live",
        reason="arq_job_pending",
        stage="mandatory_coverage",
        age_seconds=900.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is False


def test_fresh_heartbeat_live_is_not_dead():
    liveness = _liveness(
        state="live",
        reason="fresh_heartbeat",
        age_seconds=20.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is False


def test_enqueue_grace_is_not_dead():
    liveness = _liveness(
        state="enqueue_grace",
        reason="queued_grace",
        stage="queued",
        heartbeat={"stage": "queued"},
        age_seconds=30.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is False


def test_settled_extracted_leftover_midstage_is_not_dead():
    """EXTRACTED 是完整落库终态，残留中间心跳不得把已完成项目判死。"""
    liveness = _liveness(state="stale", stage="scoring_extract")
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTED) is False


def test_already_failed_is_not_redeclared():
    liveness = _liveness(state="stale", stage="mandatory_coverage")
    assert should_declare_extract_dead(liveness, ProjectStatus.FAILED) is False


def test_missing_while_extracting_is_dead():
    """已进入 EXTRACTING 但心跳与本地任务都消失 → 执行体不在。"""
    liveness = _liveness(state="missing", reason="no_heartbeat_or_task", heartbeat=None, age_seconds=None)
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is True


def test_missing_while_uploading_is_not_dead():
    """从未启动过的项目保持 idle，不得当成死亡。"""
    liveness = _liveness(state="missing", reason="no_heartbeat_or_task", heartbeat=None, age_seconds=None)
    assert should_declare_extract_dead(liveness, ProjectStatus.UPLOADING) is False


def test_terminal_failed_heartbeat_is_not_redeclared():
    """中文失败心跳已是终态，由状态接口直接返回 failed，不必再走宣告死亡。"""
    liveness = _liveness(
        state="terminal",
        stage="失败",
        reason="失败",
        heartbeat={"stage": "失败", "progress": "错误: LLM 调用超时"},
        age_seconds=10.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.UPLOADING) is False
    assert extract_heartbeat_is_midstage(liveness.heartbeat) is False


def test_terminal_completed_while_extracting_is_not_dead():
    """重新提取窗口：项目已 EXTRACTING，残留 completed 心跳不得判死。"""
    liveness = _liveness(
        state="terminal",
        stage="completed",
        reason="completed",
        heartbeat={"stage": "completed", "progress": "100%|完成，共12条"},
        age_seconds=5.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is False


def test_worker_missing_stale_is_dead():
    liveness = _liveness(
        state="stale",
        reason="worker_missing:extract_requirements_job",
        stage="mandatory_coverage",
        age_seconds=40.0,
    )
    assert should_declare_extract_dead(liveness, ProjectStatus.EXTRACTING) is True


def test_midstage_helper_rejects_terminals():
    assert extract_heartbeat_is_midstage({"stage": "mandatory_coverage"}) is True
    assert extract_heartbeat_is_midstage({"stage": "scoring_extract"}) is True
    assert extract_heartbeat_is_midstage({"stage": "失败"}) is False
    assert extract_heartbeat_is_midstage({"stage": "completed"}) is False
    assert extract_heartbeat_is_midstage(None) is False


def test_missing_heartbeat_rescued_when_arq_job_confirmed():
    """心跳 TTL 丢了仍能用 ARQ 未终局键证明执行体还在，不得判死。"""
    with (
        patch("app.core.tasks.get_task_heartbeat", return_value=None),
        patch(
            "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
            return_value=True,
        ),
    ):
        result = inspect_extract_task_liveness("extract_project_1528")
    assert result.state == "live"
    assert result.reason == "arq_job_pending"
    assert should_declare_extract_dead(result, ProjectStatus.EXTRACTING) is False


def test_missing_heartbeat_without_arq_is_dead_when_extracting():
    """EXTRACTING 且心跳、本地 task、ARQ 都无法证实 → 执行体不在。"""
    with (
        patch("app.core.tasks.get_task_heartbeat", return_value=None),
        patch(
            "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
            return_value=False,
        ),
    ):
        result = inspect_extract_task_liveness("extract_project_1528")
    assert result.state == "missing"
    assert should_declare_extract_dead(result, ProjectStatus.EXTRACTING) is True
    assert should_declare_extract_dead(result, ProjectStatus.UPLOADING) is False


def test_asyncio_fallback_stale_is_not_invented_as_arq_life():
    """本地兜底没有队列 job，禁止用 task_name 补探把已死 task 救活。"""
    heartbeat = {
        "stage": "scoring_extract",
        "progress": "88%|评分项提取中",
        "last_heartbeat": time.time() - 900,
        "queue_mode": "asyncio_fallback",
    }
    with patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat):
        result = inspect_extract_task_liveness("extract_project_1528")
    assert result.state == "stale"
    assert should_declare_extract_dead(result, ProjectStatus.EXTRACTING) is True
