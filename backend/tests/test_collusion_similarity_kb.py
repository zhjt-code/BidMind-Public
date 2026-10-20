import numpy as np

from app.core.collusion.similarity import _extract_top_k_pairs


def test_extract_top_k_pairs_marks_known_normal_similarity_as_boilerplate() -> None:
    sim_matrix = np.array([[0.86]], dtype=np.float32)
    chunks_a = [{
        "id": 1,
        "content": "安全生产管理体系及质量保证措施",
        "page_num": 3,
    }]
    chunks_b = [{
        "id": 2,
        "content": "安全生产管理体系及质量保证措施",
        "page_num": 5,
    }]

    results = _extract_top_k_pairs(
        sim_matrix=sim_matrix,
        chunks_a=chunks_a,
        chunks_b=chunks_b,
        top_k=5,
        min_score=0.4,
    )

    assert len(results) == 1
    assert results[0]["kb_rule_match"] is True
    assert results[0]["boilerplate_flag"] is True


def test_extract_top_k_pairs_respects_min_score_floor() -> None:
    """: 片段证据下限收紧后，低于阈值的近邻对必须被排除。

 映射风险：0.4 偏弱的最近邻硬配对会把"只在单边出现的条款"配上语义沾边的
 片段，诱发 LLM 脑补雷同。floor=0.6 时 0.5 的对必须落选，0.7 的对保留。
 """
    sim_matrix = np.array([[0.5, 0.7]], dtype=np.float32)
    chunks_a = [{"id": 1, "content": "甲方独有条款A的具体内容描述", "page_num": 1}]
    chunks_b = [
        {"id": 11, "content": "与条款A语义沾边但并不相同的内容", "page_num": 1},
        {"id": 12, "content": "真正高度相似的雷同内容片段", "page_num": 2},
    ]

    results = _extract_top_k_pairs(
        sim_matrix=sim_matrix,
        chunks_a=chunks_a,
        chunks_b=chunks_b,
        top_k=5,
        min_score=0.6,
    )

    assert len(results) == 1
    assert results[0]["chunk_b_id"] == 12
    assert results[0]["score"] >= 0.6


def test_similarity_uses_dedicated_chunk_floor_not_med_thresh() -> None:
    """静态守护：similarity.py 片段抽取必须用独立 chunk 阈值，禁止回退到 med_thresh。"""
    import inspect
    from app.core.collusion import similarity
    from app.core.config import settings

    src = inspect.getsource(similarity.batch_compute_similarity)
    assert "COLLUSION_CHUNK_PAIR_MIN_SCORE" in src
    assert settings.COLLUSION_CHUNK_PAIR_MIN_SCORE == 0.6
