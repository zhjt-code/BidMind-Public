# -*- coding: utf-8 -*-
"""SchemeReader 集成薄层测试（阶段2: off/shadow/enforce 模式行为）。

防御目标:
- off 模式零 LLM 调用零 DB 查询(等价现行为);
- shadow 模式判读但禁止改值;
- enforce 模式贴标, 但置信度低于阈值时降级为观察;
- db 缺失/信号缺失 fail-open。
"""

import pytest

from app.core.extractor.scoring.scheme_reader.integration import (
    apply_scheme_reader_attribution,
    resolve_scheme_reader_mode,
)
from app.schemas.requirement import ExtractedScoringItem

from tests.test_scheme_reader import SIGNALS_2250, _FakeLLM, _scheme_map_2250

pytestmark = pytest.mark.scoring


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, signals):
        self._signals = signals
        self.query_count = 0

    async def execute(self, _stmt):
        self.query_count += 1
        return _FakeScalarResult(self._signals)


def _items_2250():
    """现行管线错误形态: 两方案混算, 全部归 scheme_1/technical。"""
    return [
        ExtractedScoringItem(
            category="COMM", score_item_name="企业资质", description="具备相关资质得满分。",
            score_weight=40, volume="technical", scheme="scheme_1", source_pages=[10],
        ),
        ExtractedScoringItem(
            category="COMM", score_item_name="类似业绩", description="每提供一项得10分。",
            score_weight=60, volume="technical", scheme="scheme_1", source_pages=[10],
        ),
        ExtractedScoringItem(
            category="TECH", score_item_name="技术方案", description="方案完整合理得满分。",
            score_weight=50, volume="technical", scheme="scheme_1", source_pages=[11],
        ),
        ExtractedScoringItem(
            category="TECH", score_item_name="实施计划", description="计划可行得满分。",
            score_weight=50, volume="technical", scheme="scheme_1", source_pages=[11],
        ),
    ]


def test_resolve_mode_falls_back_to_shadow_on_invalid(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "banana")
    assert resolve_scheme_reader_mode() == "shadow"


@pytest.mark.asyncio
async def test_off_mode_makes_no_calls(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "off")
    db = _FakeDB(SIGNALS_2250)
    llm = _FakeLLM(response=_scheme_map_2250())
    context = await apply_scheme_reader_attribution(
        db=db, document_id=1, llm_client=llm, items=_items_2250()
    )
    assert context.mode == "off"
    assert db.query_count == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_shadow_mode_records_divergence_without_mutation(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "shadow")
    items = _items_2250()
    context = await apply_scheme_reader_attribution(
        db=_FakeDB(SIGNALS_2250), document_id=1,
        llm_client=_FakeLLM(response=_scheme_map_2250()), items=items,
    )
    assert context.usable
    assert context.attribution.stamped == 0
    assert context.attribution.divergences, "2250形态必须暴露归属分歧"
    assert all(i.volume == "technical" for i in items), "shadow 禁止改值"


@pytest.mark.asyncio
async def test_enforce_mode_stamps_items(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "enforce")
    items = _items_2250()
    context = await apply_scheme_reader_attribution(
        db=_FakeDB(SIGNALS_2250), document_id=1,
        llm_client=_FakeLLM(response=_scheme_map_2250()), items=items,
    )
    assert context.attribution.stamped >= 2
    by_name = {i.score_item_name: i for i in items}
    # 修正形态: 商务表条目归 scheme_1/commercial, 技术表条目归 scheme_2/technical
    assert by_name["企业资质"].volume == "commercial"
    assert by_name["企业资质"].scheme == "scheme_1"
    assert by_name["技术方案"].volume == "technical"
    assert by_name["技术方案"].scheme == "scheme_2"


@pytest.mark.asyncio
async def test_enforce_downgrades_below_confidence_threshold(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "enforce")
    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MIN_CONFIDENCE", 0.95)
    items = _items_2250()
    context = await apply_scheme_reader_attribution(
        db=_FakeDB(SIGNALS_2250), document_id=1,
        llm_client=_FakeLLM(response=_scheme_map_2250()), items=items,
    )
    # confidence=0.9 < 0.95 → 降级为观察
    assert context.attribution.stamped == 0
    assert all(i.volume == "technical" for i in items)


@pytest.mark.asyncio
async def test_fail_open_without_db_or_signals(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCORING_SCHEME_READER_MODE", "shadow")
    llm = _FakeLLM(response=_scheme_map_2250())
    context = await apply_scheme_reader_attribution(
        db=None, document_id=1, llm_client=llm, items=_items_2250()
    )
    assert context.reading is None
    assert llm.calls == []

    context2 = await apply_scheme_reader_attribution(
        db=_FakeDB(None), document_id=1, llm_client=llm, items=_items_2250()
    )
    assert context2.reading is None
    assert llm.calls == []
