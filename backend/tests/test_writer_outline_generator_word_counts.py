from types import SimpleNamespace

from app.core.writer.outline_generator import OutlineGenerator
from app.core.writer.prompts.outline_generate import (
    get_outline_generate_prompt,
    get_section_fill_prompt,
    get_skeleton_enrich_prompt,
)
from app.schemas.writing import OutlineNodeGenerated


def test_outline_prompts_require_target_word_count_for_leaves() -> None:
    outline_system, outline_user = get_outline_generate_prompt("", "", "", "", "")
    section_system, section_user = get_section_fill_prompt(
        "技术标", "technical", "", "", "", "", ""
    )
    skeleton_system, skeleton_user = get_skeleton_enrich_prompt(
        "技术方案", 1, "技术标", "", "", "", ""
    )

    combined = "\n".join([
        outline_system,
        outline_user,
        section_system,
        section_user,
        skeleton_system,
        skeleton_user,
    ])

    assert "target_word_count" in combined
    assert "叶子节点" in combined


def test_outline_node_generated_parses_target_word_count_from_string() -> None:
    node = OutlineNodeGenerated(
        title="项目实施方案",
        depth=0,
        sort_order=0,
        node_type="free_writing",
        target_word_count="2400",
        children=[],
    )

    assert node.target_word_count == 2400


def test_assign_word_counts_tree_prefers_llm_suggestions_with_type_caps() -> None:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    nodes = [
        OutlineNodeGenerated(
            title="资格证明材料",
            depth=0,
            sort_order=0,
            node_type="data_fill",
            target_word_count=8000,
            children=[],
        ),
        OutlineNodeGenerated(
            title="项目实施方案",
            depth=0,
            sort_order=1,
            node_type="free_writing",
            target_word_count=9000,
            children=[],
        ),
    ]

    generator._assign_word_counts_tree(nodes, total_budget=30000)

    assert generator._get_word_count(nodes[0]) == 3000
    assert generator._get_word_count(nodes[1]) == 5000


def test_assign_word_counts_tree_falls_back_when_llm_missing_suggestion() -> None:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    node = OutlineNodeGenerated(
        title="服务方案",
        depth=0,
        sort_order=0,
        node_type="free_writing",
        score_weight=2.0,
        children=[],
    )

    generator._assign_word_counts_tree([node], total_budget=3000)

    assert generator._get_word_count(node) == 3000
