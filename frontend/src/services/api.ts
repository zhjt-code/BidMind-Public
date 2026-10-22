/**
 * @deprecated 仅作为向后兼容的 barrel 保留。
 *
 * 新代码请直接从领域文件导入：
 *   - `api` / `setNavigate`           → `@/services/httpClient`
 *   - `ApiResponse` / `PaginatedResponse` → `@/services/apiTypes`
 *   - `WritingClause`                 → `@/services/writingClauseTransforms`
 *   - `WritingRetryState`             → `@/services/writingRetryTransforms`
 *   - `projectService`                → `@/services/projectApi`
 *   - `documentService` + 处理状态类型 → `@/services/documentApi`
 *   - `requirementService`            → `@/services/requirementApi`
 *   - `reviewService` + 审查状态类型  → `@/services/reviewApi`
 *   - `settingsService` + 配置/API Key 类型 → `@/services/settingsApi`
 *   - `authService`                   → `@/services/authApi`
 *   - `userService`                   → `@/services/userApi`
 *   - `collusionService` + 串标类型族 → `@/services/collusionApi`
 *   - `feedbackService` + 反馈类型    → `@/services/feedbackApi`
 *   - `auditService`                  → `@/services/auditApi`
 *   - `opsService` + 运营指标类型族   → `@/services/opsApi`
 *   - `errorLogService`               → `@/services/errorLogApi`
 *   - `knowledgeBaseService` + 知识库类型 → `@/services/knowledgeBaseApi`
 *   - `writingService` + 写标类型族   → `@/services/writingApi`
 *   - `llmCenterService` + LLM 中枢类型 → `@/services/llmCenterApi`
 *   - `messageService` + 消息类型     → `@/services/messageApi`
 *   - `bannerService` + `BannerItem` / `BroadcastLevel` → `@/services/bannerApi`
 *   - `broadcastService` + 广播 payload 类型 → `@/services/broadcastApi`
 *
 * 该 barrel 不会再被新增引用；现存引用仅供回滚兜底。
 */

import { api } from './httpClient'

export { api, setNavigate } from './httpClient'
export type { ApiResponse, PaginatedResponse } from './apiTypes'
export type { WritingClause } from './writingClauseTransforms'
export type { WritingRetryState } from './writingRetryTransforms'

export { projectService } from './projectApi'
export { documentService } from './documentApi'
export type { ProcessStageData, VectorJobStatus, ProcessStatusData } from './documentApi'
export { requirementService } from './requirementApi'
export { reviewService } from './reviewApi'
export type { ReviewStepStatus, ReviewStatus } from './reviewApi'
export { settingsService } from './settingsApi'
export type {
  ApiKeyInfo,
  ApiKeyCreateItem,
  ModelConfigData,
  ModelPurpose,
  AISettingsData,
} from './settingsApi'
export { authService } from './authApi'
export { userService } from './userApi'
export {
  collusionService,
  transformPairResult,
} from './collusionApi'
export type {
  CollusionStepStatus,
  CollusionStatus,
  EvidenceRef,
  CollusionFinding,
  AttentionItem,
  CollusionPairResult,
  GroupRoleItem,
  CollusionGroupResult,
  GlobalFindingItem,
  CoverageStats,
  SuspectedGroupSummary,
  CollusionResults,
} from './collusionApi'
export { feedbackService } from './feedbackApi'
export type { FeedbackItem, FeedbackListResponse } from './feedbackApi'
export { auditService } from './auditApi'
export { opsService } from './opsApi'
export type {
  OpsMetricItem,
  OpsOverviewResponse,
  FlowFunnelItem,
  FlowFunnelResponse,
  MetricCatalogItem,
  MetricSummaryItem,
  HotspotItem,
  HotspotsResponse,
  MetricRuleItem,
  WorkerHealthLevel,
  WorkerHealthItem,
  WorkerResourceSnapshot,
  WorkerActionItem,
  WorkerHealthResponse,
} from './opsApi'
export { errorLogService } from './errorLogApi'
export { knowledgeBaseService } from './knowledgeBaseApi'
export type {
  KnowledgeDocument,
  KnowledgeAtom,
  KnowledgeProjectionResult,
  KnowledgeDocumentUpdatePayload,
  KnowledgeUsageConfig,
  KnowledgeUsageUpdatePayload,
} from './knowledgeBaseApi'
export { writingService } from './writingApi'
export type {
  WritingProjectStepStatus,
  WritingProjectStatus,
  OutlineNode,
  WritingMaterial,
  WritingMaterialImageAsset,
  WritingMaterialTitleBatchUpdateResult,
  MaterialBatchStatus,
  WritingRetryResult,
  WritingBinding,
  GateFlagEntry,
  SectionDraft,
  ConsistencyIssue,
  MaterialChecklistItem,
} from './writingApi'
export { llmCenterService } from './llmCenterApi'
export type {
  LlmFlowSummary,
  LlmNodeSummary,
  LlmStageInfo,
  LlmEdgeInfo,
  LlmFlowTopology,
  LlmTemplateVersion,
  LlmParamDef,
  LlmNodeDetail,
  LlmRegistryStatus,
  BatchScopeRequest,
  BatchPublishPassedRequest,
  BatchRunItemCheck,
  BatchRunItem,
  BatchRunSummary,
  BatchRunResponse,
} from './llmCenterApi'
export { messageService } from './messageApi'
export type { MessageItem, MessageListResponse } from './messageApi'
export { bannerService } from './bannerApi'
export type { BroadcastLevel, BannerItem } from './bannerApi'
export { broadcastService } from './broadcastApi'
export type {
  BroadcastResponse,
  BroadcastCreatePayload,
  BroadcastUpdatePayload,
} from './broadcastApi'

export default api
