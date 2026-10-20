""": 无图资产时清洗成图表述与假 figure 标签。"""

from app.core.writer.visuals.materializer import DiagramAsset
from app.core.writer.visuals.placement_enforcer import (
    sanitize_ungrounded_diagram_claims,
)


def _asset() -> DiagramAsset:
    return DiagramAsset(
        diagram_id=6,
        visual_id="vis_1",
        diagram_type="gantt",
        title="90天工期保障甘特图",
        png_path="storage/uploads/writing_diagrams/1/2/vis_1_abc.png",
    )


def test_drops_non_numeric_diagram_figure_without_assets() -> None:
    html = (
        "<p>我方设置里程碑节点。</p>"
        '<figure data-diagram-id="gantt-90day"></figure>'
        "<h4>分阶段验收</h4>"
    )
    result = sanitize_ungrounded_diagram_claims(html, [])
    assert "data-diagram-id" not in result
    assert "gantt-90day" not in result
    assert "分阶段验收" in result


def test_strips_as_shown_and_attachment_gantt_without_assets() -> None:
    html = (
        "<p>具体架构如下图所示：</p>"
        "<p>详细甘特图作为本方案附件一并提交。</p>"
        "<p>将在项目启动后以甘特图形式提交甲方备案。</p>"
        "<p>详见附件一：甘特图，明确关键路径。</p>"
        "<p>每周对照甘特图检查各节点完成情况。</p>"
    )
    result = sanitize_ungrounded_diagram_claims(html, [])
    assert "如下图所示" not in result
    assert "作为本方案附件一并提交" not in result
    assert "以甘特图形式提交" not in result
    assert "详见附件一：甘特图" not in result
    assert "对照甘特图检查" in result


def test_drops_fake_figure_even_when_numeric_asset_exists() -> None:
    html = (
        '<figure data-diagram-id="gantt-90day"></figure>'
        '<figure data-diagram-id="6"><figcaption>90天工期保障甘特图</figcaption></figure>'
    )
    result = sanitize_ungrounded_diagram_claims(html, [_asset()])
    assert "gantt-90day" not in result
    assert 'data-diagram-id="6"' in result


def test_keeps_numeric_figure_when_asset_exists() -> None:
    html = (
        "<p>进度安排如下图所示：</p>"
        '<figure data-diagram-id="6"><figcaption>90天工期保障甘特图</figcaption></figure>'
    )
    result = sanitize_ungrounded_diagram_claims(html, [_asset()])
    assert 'data-diagram-id="6"' in result
    assert "如下图所示" in result
