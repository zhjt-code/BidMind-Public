"""换轴重构 P5：旧裁决链冻结与新轴隔离 CI 禁入测试。

职责: 锁定冻结窗口不变量——①旧裁决链模式默认不得回到 enforce；②新账本子包
      不得反向依赖任何待退役旧裁决模块；③账本报告字段全链在位；
      ④已按 ADR §4 物理删除的模块不得复活。
不做: 不测试账本算法正确性（由 test_scoring_ledger_* 覆盖）。

2026-08-02 治理阶段 2: candidate_transaction 已整包删除（含 config 开关、
API 字段、documents 列与前端消费），故其模式默认值断言移除，
改由 test_deleted_frozen_modules_stay_deleted 锁定"不得复活"。

2026-08-03: 阶段 2 遗留的生产者 scoring_table_evidence（删消费者未删生产者形成的
死数据）一并删除，由 test_scoring_table_evidence_write_path_stays_deleted 锁定。
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("JWT_SECRET_KEY", "freeze-test-only")
sys.path.insert(0, str(Path(__file__).parent.parent))

_LEDGER_DIR = (
    Path(__file__).parent.parent / "app" / "core" / "extractor" / "scoring" / "ledger"
)
# 限时冻结删除清单（ADR 2026-07-28）: 新账本禁止依赖这些待退役模块
_FROZEN_MODULES = (
    "candidate_transaction",
    "scheme_reader",
    "scheme_segmentation",
    "scoring_full_candidate",
    "scoring_rescue_pipeline",
    "scoring_rescue_integration",
)


def test_legacy_adjudication_modes_default_to_shadow():
    """红色清单: 旧裁决链默认模式不得为 enforce（切换须真值+留出集准入）。"""
    from app.core.config import Settings

    fields = Settings.model_fields
    for name in ("SCORING_SCHEME_READER_MODE",):
        default = fields[name].default
        assert default != "enforce", f"{name} 默认值禁止为 enforce"


def test_deleted_frozen_modules_stay_deleted():
    """已按 ADR §4 物理删除的模块不得复活（含其配置开关与 API 字段）。

    candidate_transaction 于 2026-08-02 治理阶段 2 跨栈删除：
    生产模式长期 off、包外 import 已全部切断、documents 列由迁移
    d1a7c93e5b02 删除、前端消费同步移除。任何形式的回流都应立即可见。
    """
    from app.core.config import Settings

    scoring_dir = (
        Path(__file__).parent.parent / "app" / "core" / "extractor" / "scoring"
    )
    assert not (scoring_dir / "candidate_transaction").exists(), (
        "candidate_transaction 包已被 ADR §4 删除，不得复活"
    )
    assert "SCORING_CANDIDATE_TRANSACTION_MODE" not in Settings.model_fields, (
        "候选事务配置开关已随包删除，不得重新声明"
    )


def test_scoring_table_evidence_write_path_stays_deleted():
    """scoring_table_evidence 死数据处置后不得回流（2026-08-03）。

    该列的唯一消费者随 candidate_transaction 于阶段 2 删除，生产者被遗留：
    每份含评分区段的文档仍在落库装配前物理表快照（实测 63/894 份、594 kB，
    约占 documents 表 25%），全仓零读者、零测试覆盖。列由迁移 b5e2f9c47a10 删除。

    三处同时锁定，任一回流即失败——曾经就是"删消费者留生产者"造成的死数据。
    """
    from app.models.document import Document

    parser_dir = Path(__file__).parent.parent / "app" / "core" / "parser"
    assert not (parser_dir / "scoring_table_evidence.py").exists(), (
        "捕获模块已删除，不得复活（无消费者的活代码是死数据的来源）"
    )
    assert not hasattr(Document, "scoring_table_evidence"), (
        "documents.scoring_table_evidence 已由迁移 b5e2f9c47a10 删除，模型不得重新声明"
    )
    chunk_stage = (
        Path(__file__).parent.parent
        / "app" / "core" / "tasks" / "process" / "chunk_stage.py"
    ).read_text(encoding="utf-8")
    assert "scoring_table_evidence" not in chunk_stage, (
        "chunk_stage 的证据落库路径已删除，不得恢复写入"
    )


def test_ledger_package_never_imports_frozen_chain():
    """新账本子包不得 import 任何待退役旧裁决模块（防新轴反向依赖旧链）。"""
    for py_file in _LEDGER_DIR.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for frozen in _FROZEN_MODULES:
            assert frozen not in source, (
                f"ledger/{py_file.name} 引用了冻结模块 {frozen}——"
                "新账本必须独立于待退役旧裁决链（ADR 2026-07-28）"
            )


def test_ledger_report_field_transported_end_to_end():
    """账本报告字段沿 service→schema→router 全链在位（P5-1 传输契约）。"""
    from app.api.routers import requirements as requirements_router
    from app.schemas import requirement as requirement_schema
    from app.services import requirement_service

    assert "scoring_ledger_report" in inspect.getsource(requirement_service)
    assert "scoring_ledger_report" in inspect.getsource(requirement_schema)
    assert "scoring_ledger_report" in inspect.getsource(requirements_router)


def test_ledger_reconciler_has_no_subset_search():
    """圆桌红线: 对账器只比对报警，禁止子集搜索凑分（kernel 不得以对账核名义迁入）。"""
    from app.core.extractor.scoring.ledger import ledger_reconciliation

    source = inspect.getsource(ledger_reconciliation)
    for forbidden in ("assign_facts_to_declarations", "_find_subsets", "dfs("):
        assert forbidden not in source, f"对账器出现凑分/指派痕迹: {forbidden}"
