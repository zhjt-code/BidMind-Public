"""_detect_section_context 防御测试。

核心风险: 历史脏数据下 chunk.chunk_index 不等于列表位置（重复/间隙），
旧实现把 chunk_index 当作 all_chunks 的下标，导致 list index out of range，
异常落入致命分支终止整个约束提取任务。

本测试锁定:
1. chunk_index 远大于列表长度时不再越界（根因回归护栏）
2. 用真实列表位置回溯章节标题，结果正确
3. 合同/履约阶段关键词能被识别
4. 目标 chunk 不在 all_chunks 时安全降级为空 section
"""

from types import SimpleNamespace

from app.core.writer.clause_extractor import ClauseExtractor


def _chunk(chunk_id: int, idx: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(id=chunk_id, chunk_index=idx, content=content)


def test_chunk_index_far_exceeds_length_does_not_raise():
    """chunk_index 越界（间隙数据）时不抛异常，仍能按真实位置回溯。"""
    all_chunks = [
        _chunk(101, 500, "第二章 投标人须知\n正文内容若干"),
        _chunk(102, 900, "本批次正文，无标题行"),
    ]

    # 旧实现会执行 all_chunks[900] -> IndexError；新实现按位置1回溯。
    section, is_contract = ClauseExtractor._detect_section_context(
        all_chunks[1], all_chunks
    )

    assert section == "第二章 投标人须知"
    assert is_contract is False


def test_locates_heading_by_real_position():
    """连续回溯能命中前置 chunk 中的章节标题。"""
    all_chunks = [
        _chunk(1, 0, "第一章 项目概况\n概况正文"),
        _chunk(2, 1, "无标题的中间块"),
        _chunk(3, 2, "继续无标题"),
        _chunk(4, 3, "目标批次正文"),
    ]

    section, is_contract = ClauseExtractor._detect_section_context(
        all_chunks[3], all_chunks
    )

    assert section == "第一章 项目概况"
    assert is_contract is False


def test_contract_phase_keyword_detected():
    """命中合同/履约阶段关键词时 is_contract 为 True。"""
    all_chunks = [
        _chunk(10, 300, "第五章 合同履约与验收要求\n验收标准如下"),
        _chunk(11, 700, "履约阶段正文"),
    ]

    section, is_contract = ClauseExtractor._detect_section_context(
        all_chunks[1], all_chunks
    )

    assert section == "第五章 合同履约与验收要求"
    assert is_contract is True


def test_target_not_in_all_chunks_returns_empty():
    """目标 chunk 无法在列表中定位时安全降级为空 section。"""
    all_chunks = [
        _chunk(1, 0, "第一章 项目概况\n正文"),
    ]
    orphan = _chunk(999, 0, "孤立块")

    section, is_contract = ClauseExtractor._detect_section_context(
        orphan, all_chunks
    )

    assert section == ""
    assert is_contract is False
