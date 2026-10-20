"""分块预算组装器(input_budget)防御测试。

覆盖语义安全收缩的关键风险点:
- 骨架永不裁、永不截断(语义正确性底线)
- 按优先级整块取舍、保持原始输出顺序
- 单块超限走首尾保留式截断而非中段腰斩
- 骨架超预算时 fail-loud(over_budget)且不破坏骨架
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.input_budget import (
    BudgetedAssembly,
    PromptBlock,
    assemble_within_budget,
    clip_text_keep_ends,
)


# --------------------------------------------------------------------------
# assemble_within_budget
# --------------------------------------------------------------------------


def test_sufficient_budget_keeps_all_in_original_order():
    """预算充足: 全部保留，且按原始顺序拼接(非优先级顺序)。"""
    blocks = [
        PromptBlock("AAA", priority=0, is_skeleton=True, label="sys"),
        PromptBlock("BBB", priority=50, label="evidence"),
        PromptBlock("CCC", priority=10, label="response"),
    ]
    out = assemble_within_budget(blocks, budget_chars=1000)

    assert out.text == "AAA\nBBB\nCCC"
    assert out.over_budget is False
    assert set(out.kept_labels) == {"sys", "evidence", "response"}
    assert out.dropped_labels == ()
    assert out.truncated_labels == ()


def test_tight_budget_drops_lowest_priority_first():
    """预算紧张: 先牺牲 priority 大(低优先级)的块，骨架与高优先级保留。"""
    blocks = [
        PromptBlock("S" * 10, is_skeleton=True, label="skeleton"),
        PromptBlock("H" * 30, priority=10, label="high", clippable=False),
        PromptBlock("L" * 30, priority=90, label="low", clippable=False),
    ]
    out = assemble_within_budget(blocks, budget_chars=10 + 1 + 30 + 1)

    assert "skeleton" in out.kept_labels
    assert "high" in out.kept_labels
    assert "low" in out.dropped_labels
    assert out.over_budget is False


def test_boundary_block_truncated_keeps_ends():
    """边界块: clippable 块放不下时首尾保留式截断，保留 START/END 与标记。"""
    body = "START" + "x" * 500 + "END"
    blocks = [
        PromptBlock("SYS", is_skeleton=True, label="sys"),
        PromptBlock(body, priority=10, label="evidence", min_keep_chars=50),
    ]
    out = assemble_within_budget(blocks, budget_chars=3 + 1 + 200)

    assert "evidence" in out.truncated_labels
    assert "START" in out.text
    assert "END" in out.text
    assert "[TRUNCATED]" in out.text
    # 允许分隔符与标记带来的少量超出
    assert out.used_chars <= 3 + 1 + 200 + 32


def test_skeleton_over_budget_is_fail_loud_and_intact():
    """骨架超预算: over_budget=True(fail-loud)，骨架完整保留不被破坏，可选块全丢。"""
    blocks = [
        PromptBlock("S" * 100, is_skeleton=True, label="sys"),
        PromptBlock("E" * 50, priority=10, label="evidence"),
    ]
    out = assemble_within_budget(blocks, budget_chars=40)

    assert out.over_budget is True
    assert out.text == "S" * 100
    assert "evidence" in out.dropped_labels
    assert out.truncated_labels == ()


def test_multiple_skeletons_intact_even_when_over_budget():
    """多骨架块: 即便合计超预算，也须按原序完整输出，绝不截断。"""
    blocks = [
        PromptBlock("HEAD" * 20, is_skeleton=True, label="sys"),
        PromptBlock("BODY" * 20, is_skeleton=True, label="requirement"),
    ]
    out = assemble_within_budget(blocks, budget_chars=10)

    assert out.over_budget is True
    assert out.text == "HEAD" * 20 + "\n" + "BODY" * 20
    assert out.truncated_labels == ()


def test_empty_blocks_returns_empty_not_over_budget():
    out = assemble_within_budget([], budget_chars=100)

    assert out.text == ""
    assert out.used_chars == 0
    assert out.over_budget is False
    assert out.kept_labels == ()


def test_blank_blocks_are_skipped():
    """纯空白块被跳过，不计入 kept。"""
    blocks = [
        PromptBlock("   ", label="blank"),
        PromptBlock("REAL", is_skeleton=True, label="real"),
    ]
    out = assemble_within_budget(blocks, budget_chars=100)

    assert out.text == "REAL"
    assert "blank" not in out.kept_labels


def test_unclippable_block_dropped_when_not_fit():
    """clippable=False 的块放不下时整块丢弃，绝不截断。"""
    blocks = [
        PromptBlock("S" * 10, is_skeleton=True, label="sys"),
        PromptBlock("X" * 100, priority=10, label="big", clippable=False),
    ]
    out = assemble_within_budget(blocks, budget_chars=50)

    assert "big" in out.dropped_labels
    assert "big" not in out.truncated_labels
    assert out.text == "S" * 10


def test_min_keep_chars_blocks_tiny_truncation():
    """剩余预算低于 min_keep_chars 时不截断(避免无意义碎片)，整块丢弃。"""
    blocks = [
        PromptBlock("S" * 10, is_skeleton=True, label="sys"),
        PromptBlock("Y" * 200, priority=10, label="ev", min_keep_chars=100),
    ]
    out = assemble_within_budget(blocks, budget_chars=10 + 1 + 30)

    assert "ev" in out.dropped_labels
    assert "ev" not in out.truncated_labels


def test_negative_budget_is_fail_loud():
    blocks = [PromptBlock("SYS", is_skeleton=True, label="sys")]
    out = assemble_within_budget(blocks, budget_chars=-1)

    assert out.over_budget is True
    assert out.text == "SYS"


def test_as_log_meta_shape():
    blocks = [
        PromptBlock("S" * 5, is_skeleton=True, label="sys"),
        PromptBlock("E" * 100, priority=10, label="ev"),
    ]
    out = assemble_within_budget(blocks, budget_chars=3)
    meta = out.as_log_meta()

    assert meta["over_budget"] is True
    assert meta["budget_chars"] == 3
    assert "ev" in meta["dropped"]
    assert isinstance(out, BudgetedAssembly)


# --------------------------------------------------------------------------
# clip_text_keep_ends
# --------------------------------------------------------------------------


def test_clip_keep_ends_preserves_head_and_tail():
    text = "HEAD" + "m" * 500 + "TAIL"
    out = clip_text_keep_ends(text, 100)

    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "[TRUNCATED]" in out


def test_clip_no_op_when_within_limit():
    assert clip_text_keep_ends("short", 100) == "short"


def test_clip_zero_or_negative_limit_returns_empty():
    assert clip_text_keep_ends("anything", 0) == ""
    assert clip_text_keep_ends("anything", -5) == ""
