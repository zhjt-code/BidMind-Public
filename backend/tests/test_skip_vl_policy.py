"""招标文件图片识别策略 -- effective_skip_vl 决策矩阵测试

覆盖:
- project_type x doc_type x requested_skip_vl -> effective_skip_vl
- 投标审查 bid 始终不跳过
- 串标审查透传用户选择
"""

import os
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.tasks.process_task import resolve_effective_skip_vl
from app.models.document import DocumentType
from app.models.project import ProjectType


class TestResolveEffectiveSkipVl:

    # ── 投标审查 ────────────────────────────────────────────────

    def test_bid_review_tender_skip_true(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_REVIEW, DocumentType.TENDER, True,
        ) is True

    def test_bid_review_tender_skip_false(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_REVIEW, DocumentType.TENDER, False,
        ) is False

    def test_bid_review_bid_always_false_even_if_requested(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_REVIEW, DocumentType.BID, True,
        ) is False

    def test_bid_review_bid_default_false(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_REVIEW, DocumentType.BID, False,
        ) is False

    # ── 标书编写 ────────────────────────────────────────────────

    def test_bid_writing_tender_skip_true(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_WRITING, DocumentType.TENDER, True,
        ) is True

    def test_bid_writing_tender_skip_false(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_WRITING, DocumentType.TENDER, False,
        ) is False

    def test_bid_writing_material_always_false(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.BID_WRITING, DocumentType.MATERIAL, True,
        ) is False

    # ── 串标审查 ────────────────────────────────────────────────

    def test_collusion_check_passthrough_true(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.COLLUSION_CHECK, DocumentType.TENDER, True,
        ) is True

    def test_collusion_check_passthrough_false(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.COLLUSION_CHECK, DocumentType.BID, False,
        ) is False

    def test_collusion_check_bid_skip_true(self) -> None:
        assert resolve_effective_skip_vl(
            ProjectType.COLLUSION_CHECK, DocumentType.BID, True,
        ) is True

    # ── 未知项目类型 ────────────────────────────────────────────

    def test_none_project_type_passthrough(self) -> None:
        assert resolve_effective_skip_vl(
            None, DocumentType.TENDER, True,
        ) is True

    def test_none_project_type_default(self) -> None:
        assert resolve_effective_skip_vl(
            None, DocumentType.BID, False,
        ) is False
