"""figure_bridge HTML 协议测试（步骤3/4）。"""


def _asset(diagram_id: int = 7, title: str = "施工管理组织流程图"):
    from app.core.writer.visuals.materializer import DiagramAsset

    return DiagramAsset(
        diagram_id=diagram_id,
        visual_id="vis_1",
        diagram_type="flowchart",
        title=title,
        png_path="storage/uploads/writing_diagrams/1/2/vis_1_abc.png",
    )


class TestAppendDiagramTags:
    def test_appends_missing_diagram_figure(self) -> None:
        from app.core.writer.visuals.figure_bridge import append_diagram_tags

        html = "<h4>组织保障</h4><p>正文内容</p>"
        result = append_diagram_tags(html, [_asset()])
        assert 'data-diagram-id="7"' in result
        assert "施工管理组织流程图" in result

    def test_does_not_duplicate_existing_figure(self) -> None:
        from app.core.writer.visuals.figure_bridge import append_diagram_tags

        html = (
            "<p>正文</p>"
            '<figure data-diagram-id="7"><figcaption>图1: 施工管理组织流程图'
            "</figcaption></figure>"
        )
        result = append_diagram_tags(html, [_asset()])
        assert result.count('data-diagram-id="7"') == 1

    def test_empty_assets_returns_html_unchanged(self) -> None:
        from app.core.writer.visuals.figure_bridge import append_diagram_tags

        html = "<p>正文</p>"
        assert append_diagram_tags(html, []) == html


class TestDiagramPlacementEnforcer:
    def test_inserts_missing_diagram_after_referenced_measure_paragraph(self) -> None:
        from app.core.writer.visuals.placement_enforcer import enforce_diagram_placement

        html = (
            "<p>本节先说明总体管理目标。</p>"
            "<p>组建现场施工管理团队，确定项目经理并明确岗位职责。</p>"
            "<p>后续从质量、安全两方面展开保障。</p>"
        )
        plan_payload = {
            "measures": [
                {
                    "measure_id": "measure_1",
                    "action": "组建现场施工管理团队",
                    "steps": ["确定项目经理", "明确岗位职责"],
                }
            ],
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "title": "施工管理组织流程图",
                    "placement_hint": "measure_1",
                    "measure_ids": ["measure_1"],
                }
            ],
        }

        result = enforce_diagram_placement(html, [_asset()], plan_payload)

        assert result.count('data-diagram-id="7"') == 1
        assert result.index('data-diagram-id="7"') > result.index("组建现场施工管理团队")
        assert result.index('data-diagram-id="7"') < result.index("后续从质量")

    def test_normalizes_variant_figure_and_removes_duplicates(self) -> None:
        from app.core.writer.visuals.placement_enforcer import enforce_diagram_placement

        html = (
            "<p>正文</p>"
            "<figure class='x' data-diagram-id='7'><figcaption>旧图题</figcaption></figure>"
            '<figure data-diagram-id="7"><figcaption>重复图题</figcaption></figure>'
        )

        result = enforce_diagram_placement(html, [_asset()], {"visuals": []})

        assert result.count('data-diagram-id="7"') == 1
        assert "旧图题" not in result
        assert "重复图题" not in result
        assert "施工管理组织流程图" in result

    def test_cleans_attachment_reference_when_inserting_in_place(self) -> None:
        from app.core.writer.visuals.placement_enforcer import enforce_diagram_placement

        html = "<p>组建现场施工管理团队，详见附件图示。</p>"
        plan_payload = {
            "measures": [
                {
                    "measure_id": "measure_1",
                    "action": "组建现场施工管理团队",
                    "steps": ["确定项目经理", "明确岗位职责"],
                }
            ],
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "title": "施工管理组织流程图",
                    "placement_hint": "measure_1",
                    "measure_ids": ["measure_1"],
                }
            ],
        }

        result = enforce_diagram_placement(html, [_asset()], plan_payload)

        assert "详见附件" not in result
        assert result.count('data-diagram-id="7"') == 1


class TestRenderDiagramDirectives:
    def test_directives_mention_id_and_protocol(self) -> None:
        from app.core.writer.visuals.materializer import render_diagram_directives

        text = render_diagram_directives([_asset()])
        assert 'data-diagram-id="7"' in text
        assert "施工管理组织流程图" in text
        assert "流程图" in text

    def test_empty_assets_returns_empty_string(self) -> None:
        from app.core.writer.visuals.materializer import render_diagram_directives

        assert render_diagram_directives([]) == ""
