"""Pydantic Schemas 模块

定义 API 请求和响应的数据模型。
"""

from app.schemas.auth import (
    UserBase,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
    Token,
    TokenRefresh,
    TokenPayload,
    PasswordChange,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectListResponse,
)
from app.schemas.document import (
    DocumentUploadResponse,
    DocumentResponse,
    DocumentListResponse,
    ProcessingResult,
)
from app.schemas.search import (
    SearchRequest,
    SearchResultItem,
    SearchResponse,
)
from app.schemas.common import (
    ApiResponse,
    PaginationParams,
    PaginatedResponse,
)
from app.schemas.requirement import (
    RequirementCategoryType,
    ExtractedRequirement,
    ExtractedRequirementList,
    RequirementCreate,
    RequirementUpdate,
    RequirementResponse,
    RequirementListResponse,
    RequirementConfirmRequest,
)
from app.schemas.bid_response import (
    EvidenceTypeValue,
    ExtractedBidResponse,
    ExtractedBidResponseNotFound,
    BidResponseCreate,
    BidResponseResponse,
    BidResponseListResponse,
    BidExtractionStatus,
)
from app.schemas.match import (
    MatchedPairResponse,
    MatchSummaryResponse,
    MatchResultResponse,
)
from app.schemas.review import (
    ComplianceLevelType,
    RiskLevelType,
    ExtractedReviewResult,
    ReviewResultResponse,
    ReviewSummaryResponse,
    ReviewResultListResponse,
    ReviewStatus,
)

__all__ = [
    # Auth schemas
    "UserBase",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "Token",
    "TokenRefresh",
    "TokenPayload",
    "PasswordChange",
    # Project schemas
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectResponse",
    "ProjectListResponse",
    # Document schemas
    "DocumentUploadResponse",
    "DocumentResponse",
    "DocumentListResponse",
    "ProcessingResult",
    # Search schemas
    "SearchRequest",
    "SearchResultItem",
    "SearchResponse",
    # Common schemas
    "ApiResponse",
    "PaginationParams",
    "PaginatedResponse",
    # Requirement schemas
    "RequirementCategoryType",
    "ExtractedRequirement",
    "ExtractedRequirementList",
    "RequirementCreate",
    "RequirementUpdate",
    "RequirementResponse",
    "RequirementListResponse",
    "RequirementConfirmRequest",
    # BidResponse schemas
    "EvidenceTypeValue",
    "ExtractedBidResponse",
    "ExtractedBidResponseNotFound",
    "BidResponseCreate",
    "BidResponseResponse",
    "BidResponseListResponse",
    "BidExtractionStatus",
    # Match schemas
    "MatchedPairResponse",
    "MatchSummaryResponse",
    "MatchResultResponse",
    # Review schemas
    "ComplianceLevelType",
    "RiskLevelType",
    "ExtractedReviewResult",
    "ReviewResultResponse",
    "ReviewSummaryResponse",
    "ReviewResultListResponse",
    "ReviewStatus",
]
