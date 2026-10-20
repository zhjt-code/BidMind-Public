"""文档齐套就绪判定：同一机制覆盖投标审查 / 写标 / 串标。"""

from app.models.document import DocumentType
from app.models.project import ProjectStatus, ProjectType
from app.services.document_parse_readiness import (
    DocumentParseFact,
    next_status_after_document_parse,
    required_documents_are_parsed,
)


def _fact(
    doc_type: DocumentType,
    parsed: int = 1,
    bid_part: str | None = None,
) -> DocumentParseFact:
    return DocumentParseFact(doc_type=doc_type, parsed=parsed, bid_part=bid_part)


def test_bid_review_pair_parsed_is_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [_fact(DocumentType.TENDER), _fact(DocumentType.BID)],
        min_collusion_bids=2,
    ) is True


def test_bid_review_split_envelope_parsed_is_ready() -> None:
    """投标信封分卷: 商务标+技术标齐套且均 parsed 才就绪。"""
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [
            _fact(DocumentType.TENDER),
            _fact(DocumentType.BID, bid_part="commercial"),
            _fact(DocumentType.BID, bid_part="technical"),
        ],
        min_collusion_bids=2,
    ) is True


def test_bid_review_split_envelope_missing_volume_is_not_ready() -> None:
    """分卷缺技术标: 即使商务标已 parsed 也不齐套(防提前 parsed/预热)。"""
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [
            _fact(DocumentType.TENDER),
            _fact(DocumentType.BID, bid_part="commercial"),
        ],
        min_collusion_bids=2,
    ) is False


def test_bid_review_split_envelope_unparsed_volume_is_not_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [
            _fact(DocumentType.TENDER),
            _fact(DocumentType.BID, bid_part="commercial"),
            _fact(DocumentType.BID, parsed=0, bid_part="technical"),
        ],
        min_collusion_bids=2,
    ) is False


def test_bid_review_duplicate_integral_bids_are_not_ready() -> None:
    """两份整本 bid 不构成合法信封(历史 bid_count>=1 口径的回归防线)。"""
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [
            _fact(DocumentType.TENDER),
            _fact(DocumentType.BID),
            _fact(DocumentType.BID),
        ],
        min_collusion_bids=2,
    ) is False


def test_bid_review_single_tender_is_not_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW,
        [_fact(DocumentType.TENDER)],
        min_collusion_bids=2,
    ) is False


def test_writing_tender_only_is_ready() -> None:
    """同形态第二例：写标只有招标文件，齐套即可进入就绪态。"""
    assert required_documents_are_parsed(
        ProjectType.BID_WRITING,
        [_fact(DocumentType.TENDER)],
        min_collusion_bids=2,
    ) is True


def test_writing_unparsed_tender_is_not_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.BID_WRITING,
        [_fact(DocumentType.TENDER, parsed=0)],
        min_collusion_bids=2,
    ) is False


def test_collusion_two_bids_are_ready() -> None:
    """同形态第三例：串标至少两份投标均已处理。"""
    assert required_documents_are_parsed(
        ProjectType.COLLUSION_CHECK,
        [_fact(DocumentType.BID), _fact(DocumentType.BID)],
        min_collusion_bids=2,
    ) is True


def test_collusion_one_bid_is_not_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.COLLUSION_CHECK,
        [_fact(DocumentType.BID)],
        min_collusion_bids=2,
    ) is False


def test_collusion_two_bids_plus_unparsed_tender_is_not_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.COLLUSION_CHECK,
        [
            _fact(DocumentType.BID),
            _fact(DocumentType.BID),
            _fact(DocumentType.TENDER, parsed=0),
        ],
        min_collusion_bids=2,
    ) is False


def test_empty_docs_never_ready() -> None:
    assert required_documents_are_parsed(
        ProjectType.BID_REVIEW, [], min_collusion_bids=2,
    ) is False


def test_upload_phase_advances_to_parsed() -> None:
    assert next_status_after_document_parse(
        ProjectStatus.UPLOADING, all_required_parsed=True,
    ) is ProjectStatus.PARSED
    assert next_status_after_document_parse(
        ProjectStatus.PARSING, all_required_parsed=True,
    ) is ProjectStatus.PARSED


def test_incomplete_docs_stay_uploading() -> None:
    assert next_status_after_document_parse(
        ProjectStatus.PARSING, all_required_parsed=False,
    ) is ProjectStatus.UPLOADING


def test_downstream_status_is_not_rewritten() -> None:
    assert next_status_after_document_parse(
        ProjectStatus.EXTRACTING, all_required_parsed=True,
    ) is None
    assert next_status_after_document_parse(
        ProjectStatus.REVIEWING, all_required_parsed=True,
    ) is None
    assert next_status_after_document_parse(
        ProjectStatus.FAILED, all_required_parsed=True,
    ) is None
