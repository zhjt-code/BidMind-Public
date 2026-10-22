"""Stage C 护栏测试: LLM 句对蕴含裁决。

覆盖:
- 按 pair_id 正确回填裁决
- 过滤不在本批请求内的串扰 pair_id(防臆造)
- 单批裁决失败被跳过而非抛出(下游保守处理)
- llm_client=None 直接返回空
"""

import pytest

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialChapterCandidate,
    MaterialPairJudgeBatch,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.pair_judge import judge_candidate_pairs


def _mat(material_id: int, **kwargs) -> MaterialSemanticProfile:
    base = dict(material_id=material_id, title=f"素材{material_id}", proposition_text=f"素材命题{material_id}")
    base.update(kwargs)
    return MaterialSemanticProfile(**base)


def _chap(node_id: int, **kwargs) -> ChapterDemandProfile:
    base = dict(node_id=node_id, title=f"章节{node_id}", demand_text=f"章节需求{node_id}")
    base.update(kwargs)
    return ChapterDemandProfile(**base)


def _cand(material_id: int, node_id: int) -> MaterialChapterCandidate:
    return MaterialChapterCandidate(material_id=material_id, node_id=node_id, recall_score=0.7)


class _FakeLLM:
    def __init__(self, batch: MaterialPairJudgeBatch) -> None:
        self._batch = batch
        self.calls = 0

    async def chat_with_validation(self, **kwargs):
        self.calls += 1
        return self._batch


@pytest.mark.asyncio
async def test_judge_maps_decisions_by_pair_id() -> None:
    candidates = [_cand(1, 10), _cand(1, 11)]
    batch = MaterialPairJudgeBatch(
        decisions=[
            {"pair_id": "m1_n10", "relation": "supports", "confidence": 0.9},
            {"pair_id": "m1_n11", "relation": "无关", "confidence": 0.2},
        ]
    )
    llm = _FakeLLM(batch)

    outcome = await judge_candidate_pairs(candidates, [_mat(1)], [_chap(10), _chap(11)], llm)

    assert outcome["m1_n10"].relation == "supports"
    assert outcome["m1_n11"].relation == "unrelated"  # 中文变体已归一
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_judge_filters_foreign_pair_ids() -> None:
    candidates = [_cand(1, 10)]
    batch = MaterialPairJudgeBatch(
        decisions=[
            {"pair_id": "m1_n10", "relation": "supports", "confidence": 0.9},
            {"pair_id": "m9_n99", "relation": "supports", "confidence": 0.9},
        ]
    )

    outcome = await judge_candidate_pairs(candidates, [_mat(1)], [_chap(10)], _FakeLLM(batch))

    assert set(outcome.keys()) == {"m1_n10"}


@pytest.mark.asyncio
async def test_judge_skips_failed_batch() -> None:
    class _RaisingLLM:
        async def chat_with_validation(self, **kwargs):
            raise RuntimeError("llm boom")

    outcome = await judge_candidate_pairs([_cand(1, 10)], [_mat(1)], [_chap(10)], _RaisingLLM())
    assert outcome == {}


@pytest.mark.asyncio
async def test_judge_none_client_returns_empty() -> None:
    outcome = await judge_candidate_pairs([_cand(1, 10)], [_mat(1)], [_chap(10)], None)
    assert outcome == {}


class _PeakLLM:
    def __init__(self, fail_material_id: int | None = None) -> None:
        self.fail_material_id = fail_material_id
        self.current = 0
        self.peak = 0
        self.calls = 0

    async def chat_with_validation(self, **kwargs):
        import asyncio
        import re

        from app.core.writer.material_matching.contracts import MaterialPairJudgeBatch

        self.calls += 1
        self.current += 1
        self.peak = max(self.peak, self.current)
        await asyncio.sleep(0.04)
        pair_ids = list(dict.fromkeys(re.findall(r"m\d+_n\d+", str(kwargs.get("user_message") or ""))))
        self.current -= 1
        if self.fail_material_id is not None:
            needle = f"m{self.fail_material_id}_"
            if any(pid.startswith(needle) for pid in pair_ids):
                raise RuntimeError("llm boom")
        return MaterialPairJudgeBatch(
            decisions=[
                {
                    "pair_id": pair_id,
                    "relation": "supports",
                    "confidence": 0.9,
                    "evidence_quote": "",
                    "reason": "ok",
                }
                for pair_id in pair_ids
            ]
            + [{"pair_id": "m99_n99", "relation": "supports", "confidence": 0.9}]
        )


@pytest.mark.asyncio
async def test_judge_parallel_matches_serial_and_bounds_peak() -> None:
    from app.core.writer.material_matching.pair_judge import (
        _PAIR_JUDGE_BATCH_CONCURRENCY,
    )

    candidates = [_cand(i, 10 + i) for i in range(1, 7)]
    materials = [_mat(i) for i in range(1, 7)]
    chapters = [_chap(10 + i) for i in range(1, 7)]
    llm = _PeakLLM()
    outcome = await judge_candidate_pairs(
        candidates, materials, chapters, llm, batch_size=1,
    )
    expected_ids = {f"m{i}_n{10 + i}" for i in range(1, 7)}
    assert set(outcome.keys()) == expected_ids
    assert all(decision.relation == "supports" for decision in outcome.values())
    assert llm.peak <= _PAIR_JUDGE_BATCH_CONCURRENCY
    assert llm.peak >= min(3, _PAIR_JUDGE_BATCH_CONCURRENCY)
    assert "m99_n99" not in outcome


@pytest.mark.asyncio
async def test_judge_failed_batch_keeps_other_batches() -> None:
    candidates = [_cand(1, 11), _cand(2, 12), _cand(3, 13)]
    materials = [_mat(1), _mat(2), _mat(3)]
    chapters = [_chap(11), _chap(12), _chap(13)]
    llm = _PeakLLM(fail_material_id=2)
    outcome = await judge_candidate_pairs(
        candidates, materials, chapters, llm, batch_size=1,
    )
    assert set(outcome.keys()) == {"m1_n11", "m3_n13"}
    assert "m2_n12" not in outcome
    assert "m99_n99" not in outcome
