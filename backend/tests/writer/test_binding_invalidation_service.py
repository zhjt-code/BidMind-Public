from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.writing_binding import BindingStatus
from app.services.writing.binding_service import BindingService


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_confirm_bindings_marks_impacted_draft_needs_review_and_invalidates_render() -> None:
    binding = SimpleNamespace(
        id=14109,
        project_id=1568,
        outline_node_id=6772,
        clause_id=None,
        material_id=726,
        status=BindingStatus.suggested,
    )
    draft = SimpleNamespace(
        outline_node_id=6772,
        review_required=0,
        failure_state="completed",
        failure_reason=None,
        is_current=1,
    )
    artifact = SimpleNamespace(
        preview_html="<html />",
        docx_blob=b"docx",
        docx_size=4,
        rendered_at="now",
        consistency_issues_json="[]",
        consistency_checked_at="now",
        consistency_render_version=1,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarRows([binding]),
                _ScalarRows([draft]),
                _ScalarRows([artifact]),
            ]
        ),
        commit=AsyncMock(),
    )
    service = BindingService(
        db,
        project_resolver=AsyncMock(return_value=SimpleNamespace(id=1568)),
    )

    count = await service.confirm_bindings(1568, [14109], SimpleNamespace())

    assert count == 1
    assert binding.status == BindingStatus.confirmed
    assert draft.review_required == 1
    assert draft.failure_state == "needs_review"
    assert "绑定已变更" in draft.failure_reason
    assert artifact.preview_html is None
    assert artifact.docx_blob is None
    assert artifact.consistency_issues_json is None
    assert artifact.consistency_render_version is None


@pytest.mark.asyncio
async def test_update_binding_invalidates_old_and_new_outline_nodes() -> None:
    binding = SimpleNamespace(
        id=14109,
        project_id=1568,
        outline_node_id=6772,
        clause_id=None,
        material_id=726,
        status=BindingStatus.confirmed,
        scope=None,
    )
    old_draft = SimpleNamespace(
        outline_node_id=6772,
        review_required=0,
        failure_state="completed",
        failure_reason=None,
        is_current=1,
    )
    new_draft = SimpleNamespace(
        outline_node_id=6784,
        review_required=0,
        failure_state="completed",
        failure_reason=None,
        is_current=1,
    )
    artifact = SimpleNamespace(
        preview_html="<html />",
        docx_blob=b"docx",
        docx_size=4,
        rendered_at="now",
        consistency_issues_json="[]",
        consistency_checked_at="now",
        consistency_render_version=1,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=binding),
        execute=AsyncMock(
            side_effect=[
                _ScalarRows([old_draft, new_draft]),
                _ScalarRows([artifact]),
            ]
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = BindingService(
        db,
        project_resolver=AsyncMock(return_value=SimpleNamespace(id=1568)),
    )

    updated = await service.update_binding(
        1568,
        14109,
        SimpleNamespace(),
        {"outline_node_id": 6784},
    )

    assert updated.outline_node_id == 6784
    assert old_draft.review_required == 1
    assert new_draft.review_required == 1
    assert old_draft.failure_state == "needs_review"
    assert new_draft.failure_state == "needs_review"
    assert artifact.preview_html is None
