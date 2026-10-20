"""写标草稿响应 HTML 协议清洗契约。"""

from app.schemas.writing import SectionDraftResponse


def test_section_draft_response_strips_markdown_html_fence() -> None:
    draft = SectionDraftResponse(
        id=1,
        outline_node_id=1,
        version=1,
        content="```html\n<p>响应服务方案。</p>\n```",
        content_format="html",
        is_current=1,
        is_user_edited=0,
        is_locked=0,
        status="completed",
    )

    assert draft.content == "<p>响应服务方案。</p>"
