from app.core.tasks.process_task import resolve_effective_skip_vl
from app.models.document import DocumentType
from app.models.project import ProjectType


def test_collusion_bid_documents_keep_requested_skip_vl() -> None:
    assert resolve_effective_skip_vl(
        ProjectType.COLLUSION_CHECK,
        DocumentType.BID,
        requested_skip_vl=True,
    ) is True
    assert resolve_effective_skip_vl(
        ProjectType.COLLUSION_CHECK,
        DocumentType.BID,
        requested_skip_vl=False,
    ) is False


def test_collusion_tender_documents_keep_requested_skip_vl() -> None:
    assert resolve_effective_skip_vl(
        ProjectType.COLLUSION_CHECK,
        DocumentType.TENDER,
        requested_skip_vl=True,
    ) is True
    assert resolve_effective_skip_vl(
        ProjectType.COLLUSION_CHECK,
        DocumentType.TENDER,
        requested_skip_vl=False,
    ) is False
