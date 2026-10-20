"""回归测试: BidDirectoryItem.order 归一化 + _build_skeleton_recursive 边界场景

覆盖: LLM 输出 order=0 导致 sort_order=-1 触发 Pydantic 校验失败
"""

from types import SimpleNamespace

from app.schemas.writing import BidDirectoryItem, OutlineNodeGenerated
from app.core.writer.outline_generator import OutlineGenerator


# ==================== BidDirectoryItem.order 归一化 ====================


def test_bid_directory_item_order_zero_normalized_to_one() -> None:
    """order=0 应被归一化为 1"""
    item = BidDirectoryItem(order=0, title="投标函")
    assert item.order == 1


def test_bid_directory_item_order_negative_normalized_to_one() -> None:
    """order=-1 应被归一化为 1"""
    item = BidDirectoryItem(order=-1, title="授权书")
    assert item.order == 1


def test_bid_directory_item_order_positive_preserved() -> None:
    """order=3 应保持不变"""
    item = BidDirectoryItem(order=3, title="技术方案")
    assert item.order == 3


def test_bid_directory_item_order_float_coerced() -> None:
    """order=2.0 应被转为 int 2"""
    item = BidDirectoryItem(order=2.0, title="资质证明")
    assert item.order == 2


# ==================== _build_skeleton_recursive 边界场景 ====================


def _make_generator() -> OutlineGenerator:
    return OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())


def test_build_skeleton_recursive_order_zero_no_crash() -> None:
    """order=0 经归一化后进入 _build_skeleton_recursive 不应抛异常"""
    gen = _make_generator()
    item = BidDirectoryItem(order=0, title="投标函", node_type="fixed_template")
    node = gen._build_skeleton_recursive(item, current_depth=1)
    assert isinstance(node, OutlineNodeGenerated)
    assert node.sort_order >= 0
    assert node.title == "投标函"


def test_build_skeleton_recursive_order_one_produces_zero() -> None:
    """order=1 应产生 sort_order=0 (1-1=0)"""
    gen = _make_generator()
    item = BidDirectoryItem(order=1, title="投标函", node_type="fixed_template")
    node = gen._build_skeleton_recursive(item, current_depth=1)
    assert node.sort_order == 0


def test_build_skeleton_recursive_with_children() -> None:
    """递归子节点 order=0 归一化后不应抛异常"""
    gen = _make_generator()
    child = BidDirectoryItem(order=0, title="承诺书", node_type="fixed_template")
    parent = BidDirectoryItem(
        order=1, title="投标函", node_type="fixed_template", children=[child],
    )
    node = gen._build_skeleton_recursive(parent, current_depth=1)
    assert len(node.children) == 1
    assert node.children[0].sort_order >= 0
    assert node.children[0].title == "承诺书"


def test_build_skeleton_recursive_preserves_volume_type() -> None:
    """volume_type 应在递归中正确传递"""
    gen = _make_generator()
    item = BidDirectoryItem(order=1, title="报价部分")
    node = gen._build_skeleton_recursive(
        item, current_depth=0, volume_type="commercial",
    )
    assert node.volume_type == "commercial"
