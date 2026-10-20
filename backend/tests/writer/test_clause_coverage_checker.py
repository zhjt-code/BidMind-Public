from types import SimpleNamespace


def _clause(
    clause_id: int,
    *,
    content: str = "约束内容",
    sub_type: str = "content_required",
    layer: str = "structure_constraint",
    confirmed: int = 1,
):
    return SimpleNamespace(
        id=clause_id,
        content=content,
        sub_type=sub_type,
        layer=layer,
        confirmed=confirmed,
    )


def _draft(source_clause_ids: str | None):
    return SimpleNamespace(source_clause_ids=source_clause_ids)


def test_build_clause_coverage_issues_reports_confirmed_visible_uncovered_clause() -> None:
    from app.core.writer.clause_coverage_checker import build_clause_coverage_issues

    issues = build_clause_coverage_issues(
        clauses=[
            _clause(1, content="技术方案必须包含安全保障措施"),
            _clause(2, content="项目背景", sub_type="project_context"),
        ],
        drafts=[_draft("[2]")],
    )

    assert len(issues) == 1
    assert issues[0]["type"] == "clause_coverage"
    assert issues[0]["severity"] == "warning"
    assert issues[0]["node_id"] is None
    assert "安全保障措施" in issues[0]["message"]
    # 用户可见 detail 不得暴露 schema 字段名
    assert "source_clause_ids" not in issues[0]["detail"]
    assert "source_material_ids" not in issues[0]["detail"]
    assert "正文来源" in issues[0]["detail"]
    assert "1" in issues[0]["detail"]


def test_build_clause_coverage_issues_detail_uses_user_facing_chinese() -> None:
    """: 覆盖缺口 detail 必须是中文业务语义，禁止英文字段名泄漏。"""
    from app.core.writer.clause_coverage_checker import build_clause_coverage_issues

    issues = build_clause_coverage_issues(
        clauses=[_clause(18279, content="投标人须具备履行合同所必需的仪器设备")],
        drafts=[],
    )

    assert len(issues) == 1
    detail = issues[0]["detail"]
    assert "18279" in detail
    assert "source_clause_ids" not in detail
    assert "schema" not in detail.lower()
    assert "绑定" in detail
    assert "响应" in detail


def test_build_clause_coverage_issues_ignores_hidden_and_unconfirmed_clauses() -> None:
    from app.core.writer.clause_coverage_checker import build_clause_coverage_issues

    issues = build_clause_coverage_issues(
        clauses=[
            _clause(1, sub_type="bid_structure_analysis"),
            _clause(2, sub_type="bid_directory_spec"),
            _clause(3, confirmed=0),
        ],
        drafts=[],
    )

    assert issues == []


def test_build_clause_coverage_issues_excludes_all_non_body_sub_types() -> None:
    """: matcher 不参与匹配的 6 类 sub_type 都不计入覆盖分母。"""
    from app.core.writer.clause_coverage_checker import (
        HIDDEN_COVERAGE_SUB_TYPES,
        build_clause_coverage_issues,
    )

    clauses = [
        _clause(idx, sub_type=sub_type, content=f"排除类型约束-{sub_type}")
        for idx, sub_type in enumerate(sorted(HIDDEN_COVERAGE_SUB_TYPES), start=1)
    ]
    issues = build_clause_coverage_issues(clauses=clauses, drafts=[])

    assert issues == []


def test_build_clause_coverage_issues_still_counts_body_sub_types() -> None:
    """正文来源类型(评分/内容/材料/废标)未覆盖时仍应报缺口。"""
    from app.core.writer.clause_coverage_checker import build_clause_coverage_issues

    issues = build_clause_coverage_issues(
        clauses=[
            _clause(1, sub_type="scoring_criterion", content="评分标准未覆盖"),
            _clause(2, sub_type="disqualification", content="废标条款未覆盖"),
            _clause(3, sub_type="material_required", content="必备材料未覆盖"),
        ],
        drafts=[],
    )

    assert len(issues) == 3


def test_build_clause_coverage_issues_excludes_reference_relevance() -> None:
    """: matcher 判定为参考的正文类型不得继续触发正文覆盖告警。"""
    from app.core.writer.clause_coverage_checker import build_clause_coverage_issues

    clause = _clause(1, sub_type="emphasis_point", content="仅供参考的写作提示")
    clause.metadata_json = {
        "writing_relevance": "reference",
        "writing_relevance_source": "matcher",
        "writing_relevance_reason": "AI判定不影响正文编写",
    }

    issues = build_clause_coverage_issues(clauses=[clause], drafts=[])

    assert issues == []


def test_coverage_exclusion_is_single_source_with_matcher() -> None:
    """: 覆盖分母排除集必须与 matcher 匹配排除集单一同源(同一对象)。"""
    from app.core.writer.clause_coverage_checker import HIDDEN_COVERAGE_SUB_TYPES
    from app.core.writer.material_matcher import MaterialMatcher

    assert MaterialMatcher._EXCLUDED_SUB_TYPES is HIDDEN_COVERAGE_SUB_TYPES


def test_parse_source_clause_ids_is_tolerant_of_empty_or_invalid_json() -> None:
    from app.core.writer.clause_coverage_checker import parse_source_clause_ids

    assert parse_source_clause_ids(None) == set()
    assert parse_source_clause_ids("") == set()
    assert parse_source_clause_ids("not-json") == set()
    assert parse_source_clause_ids("[1, \"2\", null, \"bad\"]") == {1, 2}


def test_section_draft_response_exposes_source_clause_ids_as_array() -> None:
    from app.schemas.writing import SectionDraftResponse

    draft = SimpleNamespace(
        id=1,
        outline_node_id=10,
        version=1,
        content="<p>正文</p>",
        content_format="html",
        summary=None,
        source_clause_ids="[1, \"2\", null, \"bad\"]",
        is_current=1,
        is_user_edited=0,
        is_locked=0,
        word_count=10,
        status="draft",
        gate_flags=None,
    )

    response = SectionDraftResponse.model_validate(draft)

    assert response.source_clause_ids == [1, 2]
