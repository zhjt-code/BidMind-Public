import { api } from './httpClient'
import { mapProgressStatusSteps, sanitizeProgressText } from './progressStatusTransforms'
import { transformWritingClause, type WritingClause } from './writingClauseTransforms'
import { transformRetryState, type WritingRetryState } from './writingRetryTransforms'

export interface WritingProjectStepStatus {
  label: string
  status: 'waiting' | 'running' | 'completed' | 'failed'
  progress: number
  detail?: string
}

export interface WritingProjectStatus {
  id: number
  name: string
  status: string
  errorMessage?: string
  overallStatus: 'idle' | 'running' | 'completed' | 'failed'
  overallProgress: number
  steps: WritingProjectStepStatus[]
  message?: string
  isRecovering: boolean
  recoveryCount: number
  currentStage: string
  currentDetail: string
}

export interface OutlineNode {
  id: number
  projectId: number
  parentId: number | null
  title: string
  depth: number
  sortOrder: number
  nodeType: string
  isRequired: number
  scoreWeight: number | null
  targetWordCount: number | null
  status: string
  volumeType: string | null
  isSkeleton: number
  chapterProfile: string | null
}

export interface WritingMaterialImageAsset {
  id: number
  caption: string | null
  imageType: string | null
  businessSubtype: string | null
  reviewStatus: string
}

export interface WritingMaterial {
  id: number
  projectId: number
  documentId: number | null
  parentMaterialId: number | null
  materialType: string
  materialRole: string
  title: string
  displayTitle: string | null
  effectiveTitle: string
  content: string | null
  filePath: string | null
  metadataTags: string | null
  sourcePage: number | null
  sourcePageStart: number | null
  sourcePageEnd: number | null
  splitConfidence: number | null
  splitReviewStatus: string
  structureHintLabel: string | null
  imageCount: number
  validatedImageCount: number
  reviewImageCount: number
  imageAssets: WritingMaterialImageAsset[]
  processingStatus: string
  kbSourceId: number | null
  kbAtomId: number | null
  kbSourceVersion: string | null
  kbSnapshotAt: string | null
  kbSourceChunkIds: string | null
}

export interface WritingMaterialTitleBatchUpdateResult {
  updatedCount: number
  updatedMaterialIds: number[]
  requiresRematch: boolean
}

export interface MaterialBatchStatus {
  total: number
  completed: number
  failed: number
  processing: number
  /**
   * 前端感知阶段：idle/parsing/vl_recognition/indexing/image_classify/tagging/done/failed。
   * 注意：idle 仅代表"当前无活跃任务"，**不得**等同于完成态，前端请统一以 phase==='done'
   * 判定完成，避免后端仍在 image_classify / tagging 期间被误认为已完成。
   */
  phase: string
  vlProgress: string
  /**
   * 后端任务是否仍活跃（ARQ/asyncio 存活 或 入队宽限期内）。
   * 为 true 时前端应继续轮询，即使 phase 短暂回落到 parsing 也不应判定完成。
   */
  taskActive: boolean
  /**
   * 后端原始心跳阶段（含 label_resolve 等内部阶段），用于 UI 细化展示，前端一般不做状态判定。
   */
  heartbeatPhase: string | null
  retryable: boolean
  failedNode: string | null
  targetId: number | null
  failedStage: string | null
  retryActionLabel: string
  preservedOutputs: string[]
  resetScope: string[]
  lastAttempt: number
  retryRunning: boolean
  lastError: string | null
}

export interface WritingRetryResult extends WritingRetryState {
  taskName: string | null
  queueMode: string
  message: string
}

export interface WritingBinding {
  id: number
  projectId: number
  outlineNodeId: number | null
  clauseId: number | null
  materialId: number | null
  bindingType: string
  confidence: number
  source: string
  status: string
  reason: string | null
  scope: string | null
}

export interface GateFlagEntry {
  verdict?: string
  severity?: string
  reason?: string
  patch?: Record<string, unknown>
  trace?: Record<string, unknown>
}

export interface SectionDraft {
  id: number
  outlineNodeId: number
  version: number
  content: string | null
  contentFormat: string
  summary: string | null
  sourceClauseIds: number[]
  sourceMaterialIds: number[]
  isCurrent: number
  isUserEdited: number
  isLocked: number
  wordCount: number | null
  status: string
  failureState: string | null
  failureReason: string | null
  reviewRequired: number | null
  // P2.1b: 单章级 Gate 信号（素材未引用 / 表格填充率 / AI 套话等）；null 表示无告警或 gate 未启用
  gateFlags: Record<string, GateFlagEntry> | null
}

export interface WritingDiagramDefinition {
  phases?: Array<{ label: string; duration_days: number; milestone?: boolean }>
  node_labels?: string[]
  org_lead?: string
  org_units?: string[]
  lanes?: string[]
  swim_nodes?: Array<{ label: string; lane: string }>
  layers?: Array<{ label: string; items?: string[] }>
  network_nodes?: Array<{ node_id?: string; label: string; duration_days: number }>
  network_edges?: Array<{ source: string; target: string }>
  wbs_root?: string
  wbs_packages?: Array<{ label: string; items?: string[] }>
  zones?: string[]
  style?: WritingDiagramStyle
}

export interface WritingDiagramStyle {
  theme: 'classic_blue' | 'business_gray' | 'tech_teal'
  primary_color?: string
  text_color?: string
  font_scale?: 'sm' | 'md' | 'lg'
}

export interface WritingDiagram {
  id: number
  diagram_type: 'flowchart' | 'gantt' | 'swimlane' | 'roadmap' | 'org_chart' | 'layered' | 'network' | 'wbs' | 'zone_layout'
  title: string
  status: string
  definition: WritingDiagramDefinition
  content_sha256: string
}

export interface WritingDiagramUpdatePayload {
  title: string
  phases?: Array<{ label: string; duration_days: number; milestone?: boolean }>
  node_labels?: string[]
  org_lead?: string
  org_units?: string[]
  lanes?: string[]
  swim_nodes?: Array<{ label: string; lane: string }>
  layers?: Array<{ label: string; items?: string[] }>
  network_nodes?: Array<{ node_id?: string; label: string; duration_days: number }>
  network_edges?: Array<{ source: string; target: string }>
  wbs_root?: string
  wbs_packages?: Array<{ label: string; items?: string[] }>
  zones?: string[]
  style?: WritingDiagramStyle
}

export interface ConsistencyIssue {
  type: string
  severity: string
  nodeId: number | null
  message: string
  detail: string
}

// 约束消费覆盖诊断类型与转换独立在 writingClauseCoverageTransforms(便于契约测试)
export type {
  ClauseCoverage,
  ClauseCoverageReasonGroup,
  ClauseCoverageSample,
} from './writingClauseCoverageTransforms'
import {
  transformClauseCoverage,
  type ClauseCoverage,
} from './writingClauseCoverageTransforms'

export interface MaterialChecklistItem {
  clauseId: number
  requirement: string
  subRequirements: string[]
  category: string
  status: string
  matchedMaterialId: number | null
}

function transformOutlineNode(raw: Record<string, unknown>): OutlineNode {
  return {
    id: raw.id as number,
    projectId: raw.project_id as number,
    parentId: (raw.parent_id as number) ?? null,
    title: raw.title as string,
    depth: raw.depth as number,
    sortOrder: raw.sort_order as number,
    nodeType: raw.node_type as string,
    isRequired: raw.is_required as number,
    scoreWeight: (raw.score_weight as number) ?? null,
    targetWordCount: (raw.target_word_count as number) ?? null,
    status: raw.status as string,
    volumeType: (raw.volume_type as string) ?? null,
    isSkeleton: (raw.is_skeleton as number) ?? 0,
    chapterProfile: (raw.chapter_profile as string) || null,
  }
}

function transformBinding(raw: Record<string, unknown>): WritingBinding {
  return {
    id: raw.id as number,
    projectId: raw.project_id as number,
    outlineNodeId: (raw.outline_node_id as number) ?? null,
    clauseId: (raw.clause_id as number) ?? null,
    materialId: (raw.material_id as number) ?? null,
    bindingType: raw.binding_type as string,
    confidence: raw.confidence as number,
    source: raw.source as string,
    status: raw.status as string,
    reason: (raw.reason as string) || null,
    scope: (raw.scope as string) || null,
  }
}

function transformDraft(raw: Record<string, unknown>): SectionDraft {
  const rawFlags = raw.gate_flags
  const gateFlags =
    rawFlags && typeof rawFlags === 'object' && !Array.isArray(rawFlags)
      ? (rawFlags as Record<string, GateFlagEntry>)
      : null
  const toIdList = (value: unknown): number[] =>
    Array.isArray(value)
      ? value.map((id) => Number(id)).filter((id) => Number.isInteger(id))
      : []
  const sourceClauseIds = toIdList(raw.source_clause_ids)
  const sourceMaterialIds = toIdList(raw.source_material_ids)
  return {
    id: raw.id as number,
    outlineNodeId: raw.outline_node_id as number,
    version: raw.version as number,
    content: (raw.content as string) || null,
    contentFormat: raw.content_format as string,
    summary: (raw.summary as string) || null,
    sourceClauseIds,
    sourceMaterialIds,
    isCurrent: raw.is_current as number,
    isUserEdited: raw.is_user_edited as number,
    isLocked: raw.is_locked as number,
    wordCount: (raw.word_count as number) ?? null,
    status: raw.status as string,
    failureState: (raw.failure_state as string) || null,
    failureReason: (raw.failure_reason as string) || null,
    reviewRequired: (raw.review_required as number) ?? null,
    gateFlags,
  }
}

function transformMaterial(raw: Record<string, unknown>): WritingMaterial {
  return {
    id: raw.id as number,
    projectId: raw.project_id as number,
    documentId: (raw.document_id as number) ?? null,
    parentMaterialId: (raw.parent_material_id as number) ?? null,
    materialType: raw.material_type as string,
    materialRole: (raw.material_role as string) || 'normal',
    title: raw.title as string,
    displayTitle: (raw.display_title as string) || null,
    effectiveTitle: ((raw.effective_title as string) || (raw.display_title as string) || (raw.title as string) || '').trim(),
    content: (raw.content as string) || null,
    filePath: (raw.file_path as string) || null,
    metadataTags: (raw.metadata_tags as string) || null,
    sourcePage: (raw.source_page as number) ?? null,
    sourcePageStart: (raw.source_page_start as number) ?? null,
    sourcePageEnd: (raw.source_page_end as number) ?? null,
    splitConfidence: (raw.split_confidence as number) ?? null,
    splitReviewStatus: (raw.split_review_status as string) || 'none',
    structureHintLabel: (raw.structure_hint_label as string) || null,
    imageCount: (raw.image_count as number) ?? 0,
    validatedImageCount: (raw.validated_image_count as number) ?? 0,
    reviewImageCount: (raw.review_image_count as number) ?? 0,
    imageAssets: Array.isArray(raw.image_assets)
      ? (raw.image_assets as Array<Record<string, unknown>>).map((asset) => ({
          id: asset.id as number,
          caption: (asset.caption as string) || null,
          imageType: (asset.image_type as string) || null,
          businessSubtype: (asset.business_subtype as string) || null,
          reviewStatus: (asset.review_status as string) || 'validated',
        }))
      : [],
    // 缺省回退为 "processing" 更安全：后端不返回状态时大多是"尚未跑完"的场景，
    // 原先默认 "completed" 会把未知态当完成态呈现，易触发前端误判。
    processingStatus: (raw.processing_status as string) || 'processing',
    kbSourceId: (raw.kb_source_id as number) ?? null,
    kbAtomId: (raw.kb_atom_id as number) ?? null,
    kbSourceVersion: (raw.kb_source_version as string) || null,
    kbSnapshotAt: (raw.kb_snapshot_at as string) || null,
    kbSourceChunkIds: (raw.kb_source_chunk_ids as string) || null,
  }
}

export const writingService = {
  getStatus: async (projectId: number): Promise<WritingProjectStatus> => {
    const raw = await api.get(`/writing/projects/${projectId}/status`) as unknown as Record<string, unknown>
    return {
      id: (raw.id as number) || projectId,
      name: (raw.name as string) || '',
      status: (raw.status as string) || '',
      errorMessage: raw.error_message as string | undefined,
      overallStatus: ((raw.overall_status as string) || 'idle') as WritingProjectStatus['overallStatus'],
      overallProgress: (raw.overall_progress as number) || 0,
      steps: mapProgressStatusSteps(raw.steps).map((step) => ({
        label: step.label,
        status: step.status as WritingProjectStepStatus['status'],
        progress: step.progress,
        detail: step.detail,
      })),
      message: sanitizeProgressText(raw.message as string | undefined) || undefined,
      isRecovering: Boolean(raw.is_recovering),
      recoveryCount: (raw.recovery_count as number) || 0,
      currentStage: (raw.current_stage as string) || '',
      currentDetail: sanitizeProgressText((raw.current_detail as string) || ''),
    }
  },

  // 约束提取
  triggerExtract: async (projectId: number) => {
    return api.post(`/writing/projects/${projectId}/extract`)
  },
  getClauses: async (projectId: number, layer?: string): Promise<WritingClause[]> => {
    const params = layer ? { layer } : {}
    const response = await api.get<WritingClause[]>(`/writing/projects/${projectId}/clauses`, { params })
    const raw = response as unknown as Record<string, unknown>[]
    return Array.isArray(raw) ? raw.map(transformWritingClause) : []
  },
  createClause: async (projectId: number, data: Record<string, unknown>) => {
    const response = await api.post(`/writing/projects/${projectId}/clauses`, data)
    return transformWritingClause(response as unknown as Record<string, unknown>)
  },
  updateClause: async (projectId: number, clauseId: number, data: Record<string, unknown>) => {
    const response = await api.put(`/writing/projects/${projectId}/clauses/${clauseId}`, data)
    return transformWritingClause(response as unknown as Record<string, unknown>)
  },
  deleteClause: async (projectId: number, clauseId: number) => {
    return api.delete(`/writing/projects/${projectId}/clauses/${clauseId}`)
  },
  confirmClauses: async (projectId: number, clauseIds: number[]) => {
    return api.post(`/writing/projects/${projectId}/clauses/confirm`, { clause_ids: clauseIds })
  },

  // 目录
  generateOutline: async (projectId: number, force?: boolean) => {
    const params = force ? '?force=true' : ''
    return api.post(`/writing/projects/${projectId}/outline/generate${params}`)
  },
  getOutline: async (projectId: number): Promise<OutlineNode[]> => {
    const response = await api.get<OutlineNode[]>(`/writing/projects/${projectId}/outline`)
    const raw = response as unknown as Record<string, unknown>[]
    return Array.isArray(raw) ? raw.map(transformOutlineNode) : []
  },
  updateNode: async (projectId: number, nodeId: number, data: Record<string, unknown>) => {
    const response = await api.put(`/writing/projects/${projectId}/outline/${nodeId}`, data)
    return transformOutlineNode(response as unknown as Record<string, unknown>)
  },
  createNode: async (projectId: number, data: Record<string, unknown>) => {
    const response = await api.post(`/writing/projects/${projectId}/outline`, data)
    return transformOutlineNode(response as unknown as Record<string, unknown>)
  },
  deleteNode: async (projectId: number, nodeId: number) => {
    return api.delete(`/writing/projects/${projectId}/outline/${nodeId}`)
  },
  reorderOutline: async (projectId: number, nodeIds: number[], parentId?: number) => {
    return api.post(`/writing/projects/${projectId}/outline/reorder`, {
      node_ids: nodeIds, parent_id: parentId ?? null,
    })
  },

  // 素材
  uploadMaterial: async (projectId: number, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/writing/projects/${projectId}/materials/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000,
    })
  },
  getMaterials: async (projectId: number): Promise<WritingMaterial[]> => {
    const response = await api.get<WritingMaterial[]>(`/writing/projects/${projectId}/materials`)
    const raw = response as unknown as Record<string, unknown>[]
    return Array.isArray(raw) ? raw.map(transformMaterial) : []
  },
  getChecklist: async (projectId: number): Promise<MaterialChecklistItem[]> => {
    const response = await api.get(`/writing/projects/${projectId}/materials/checklist`)
    const raw = response as unknown as Array<Record<string, unknown>>
    return raw.map((item) => ({
      clauseId: item.clause_id as number,
      requirement: item.requirement as string,
      subRequirements: (item.sub_requirements as string[]) || [],
      category: (item.category as string) || 'material',
      status: item.status as string,
      matchedMaterialId: item.matched_material_id as number | null,
    }))
  },
  deleteMaterial: async (projectId: number, materialId: number) => {
    return api.delete(`/writing/projects/${projectId}/materials/${materialId}`)
  },
  updateMaterial: async (projectId: number, materialId: number, data: { action: string }) => {
    const response = await api.put(`/writing/projects/${projectId}/materials/${materialId}`, data)
    return transformMaterial(response as unknown as Record<string, unknown>)
  },
  updateMaterialTitle: async (projectId: number, materialId: number, displayTitle: string) => {
    const response = await api.patch(`/writing/projects/${projectId}/materials/${materialId}/title`, {
      display_title: displayTitle,
    })
    return transformMaterial(response as unknown as Record<string, unknown>)
  },
  batchUpdateMaterialTitles: async (
    projectId: number,
    items: Array<{ materialId: number; displayTitle: string }>,
  ): Promise<WritingMaterialTitleBatchUpdateResult> => {
    const raw = await api.post(
      `/writing/projects/${projectId}/materials/titles/batch`,
      {
        items: items.map((item) => ({
          material_id: item.materialId,
          display_title: item.displayTitle,
        })),
      },
    ) as unknown as Record<string, unknown>
    return {
      updatedCount: (raw.updated_count as number) || 0,
      updatedMaterialIds: (raw.updated_material_ids as number[]) || [],
      requiresRematch: Boolean(raw.requires_rematch),
    }
  },
  getMaterialPreviewUrl: (projectId: number, materialId: number): string =>
    `/api/writing/projects/${projectId}/materials/${materialId}/preview`,
  getMaterialImageUrl: (projectId: number, imageId: number): string =>
    `/api/writing/projects/${projectId}/images/${imageId}`,
  processMaterialsBatch: async (projectId: number) => {
    return api.post(`/writing/projects/${projectId}/materials/process-batch`, null, {
      timeout: 600000,
    })
  },
  getMaterialsProcessStatus: async (projectId: number): Promise<MaterialBatchStatus> => {
    const raw = await api.get(`/writing/projects/${projectId}/materials/process-status`, {
      timeout: 10000,
    }) as unknown as Record<string, unknown>
    const retryState = transformRetryState(raw)
    return {
      total: (raw.total as number) || 0,
      completed: (raw.completed as number) || 0,
      failed: (raw.failed as number) || 0,
      processing: (raw.processing as number) || 0,
      phase: (raw.phase as string) || 'idle',
      vlProgress: (raw.vl_progress as string) || '',
      taskActive: Boolean(raw.task_active),
      heartbeatPhase: (raw.heartbeat_phase as string | null) ?? null,
      ...retryState,
    }
  },
  getRetryState: async (
    projectId: number,
    nodeKey = 'writing_material_process',
    targetId?: number,
  ): Promise<WritingRetryState> => {
    const raw = await api.get(`/writing/projects/${projectId}/retry-state`, {
      params: { node_key: nodeKey, target_id: targetId },
      timeout: 10000,
    }) as unknown as Record<string, unknown>
    return transformRetryState(raw)
  },
  retryWritingNode: async (
    projectId: number,
    nodeKey = 'writing_material_process',
    targetId?: number,
  ): Promise<WritingRetryResult> => {
    const raw = await api.post(`/writing/projects/${projectId}/retry`, {
      node_key: nodeKey,
      target_id: targetId,
    }) as unknown as Record<string, unknown>
    return {
      ...transformRetryState(raw),
      taskName: (raw.task_name as string | null) ?? null,
      queueMode: (raw.queue_mode as string) || '',
      message: (raw.message as string) || '',
    }
  },

  // 匹配
  triggerMatch: async (projectId: number, options?: { resetReviewedBindings?: boolean }) => {
    const query = options?.resetReviewedBindings ? '?reset_reviewed_bindings=true' : ''
    return api.post(`/writing/projects/${projectId}/match${query}`)
  },
  getBindings: async (projectId: number): Promise<WritingBinding[]> => {
    const response = await api.get<WritingBinding[]>(`/writing/projects/${projectId}/bindings`)
    const raw = response as unknown as Record<string, unknown>[]
    return Array.isArray(raw) ? raw.map(transformBinding) : []
  },
  updateBinding: async (projectId: number, bindingId: number, data: Record<string, unknown>) => {
    const response = await api.put(`/writing/projects/${projectId}/bindings/${bindingId}`, data)
    return transformBinding(response as unknown as Record<string, unknown>)
  },
  confirmBindings: async (projectId: number, bindingIds: number[]): Promise<{ confirmed: number }> => {
    const response = await api.post(`/writing/projects/${projectId}/bindings/confirm`, { binding_ids: bindingIds })
    const raw = response as unknown as Record<string, unknown>
    return { confirmed: (raw.confirmed as number) ?? 0 }
  },

  // 内容生成
  triggerCompose: async (projectId: number, force = false) => {
    const params = force ? '?force=true' : ''
    return api.post(`/writing/projects/${projectId}/compose${params}`, null, { timeout: 60000 })
  },
  getComposeStatus: async (projectId: number): Promise<{
    active: boolean
    stage: string | null
    progress: string | null
    progressPct: number
    currentDetail: string
  }> => {
    const raw = await api.get(`/writing/projects/${projectId}/compose/status`) as unknown as Record<string, unknown>
    return {
      active: (raw.active as boolean) ?? false,
      stage: (raw.stage as string | null) ?? null,
      progress: (raw.progress as string | null) ?? null,
      progressPct: (raw.progress_pct as number) ?? 0,
      currentDetail: sanitizeProgressText((raw.current_detail as string) ?? ''),
    }
  },
  composeSingle: async (
    projectId: number,
    nodeId: number,
  ): Promise<{ taskName: string; queueMode: string; message: string }> => {
    const raw = await api.post(
      `/writing/projects/${projectId}/compose/${nodeId}`,
    ) as unknown as Record<string, unknown>
    return {
      taskName: (raw.task_name as string) ?? '',
      queueMode: (raw.queue_mode as string) ?? '',
      message: (raw.message as string) ?? '',
    }
  },
  getComposeSingleStatus: async (
    projectId: number,
    nodeId: number,
  ): Promise<{
    active: boolean
    stage: string
    progressPct: number
    currentDetail: string
  }> => {
    const raw = await api.get(
      `/writing/projects/${projectId}/compose/${nodeId}/status`,
    ) as unknown as Record<string, unknown>
    return {
      active: (raw.active as boolean) ?? false,
      stage: (raw.stage as string) ?? '',
      progressPct: (raw.progress_pct as number) ?? 0,
      currentDetail: sanitizeProgressText((raw.current_detail as string) ?? ''),
    }
  },
  getDrafts: async (projectId: number): Promise<SectionDraft[]> => {
    const response = await api.get<SectionDraft[]>(`/writing/projects/${projectId}/drafts`)
    const raw = response as unknown as Record<string, unknown>[]
    return Array.isArray(raw) ? raw.map(transformDraft) : []
  },
  getDraft: async (projectId: number, nodeId: number): Promise<SectionDraft> => {
    const response = await api.get(`/writing/projects/${projectId}/drafts/${nodeId}`)
    return transformDraft(response as unknown as Record<string, unknown>)
  },
  updateDraft: async (projectId: number, nodeId: number, content: string): Promise<SectionDraft> => {
    const response = await api.put(`/writing/projects/${projectId}/drafts/${nodeId}`, { content })
    return transformDraft(response as unknown as Record<string, unknown>)
  },
  toggleLock: async (projectId: number, nodeId: number): Promise<SectionDraft> => {
    const response = await api.post(`/writing/projects/${projectId}/drafts/${nodeId}/lock`)
    return transformDraft(response as unknown as Record<string, unknown>)
  },
  getConsistency: async (projectId: number): Promise<ConsistencyIssue[]> => {
    const response = await api.get(`/writing/projects/${projectId}/consistency`)
    return response as unknown as ConsistencyIssue[]
  },
  getScoringReport: async (projectId: number): Promise<unknown> => {
    return api.get(`/writing/projects/${projectId}/clauses/scoring-report`)
  },
  getClauseCoverage: async (projectId: number): Promise<ClauseCoverage> => {
    const raw = (await api.get(
      `/writing/projects/${projectId}/clause-coverage`,
    )) as unknown as Record<string, unknown>
    return transformClauseCoverage(raw)
  },

  // 素材图片
  getNodeImages: async (projectId: number, nodeId: number) => {
    const response = await api.get(`/writing/projects/${projectId}/nodes/${nodeId}/images`)
    return response as unknown as { id: number; caption: string | null; imageType: string | null; width: number; height: number }[]
  },
  // 带鉴权获取图片二进制(图片接口走 Bearer 鉴权, 原生 <img src> 无法携带 token)
  fetchImageBlob: async (projectId: number, imageId: number): Promise<Blob> => {
    const response = await api.get<Blob>(
      `/writing/projects/${projectId}/images/${imageId}`,
      { responseType: 'blob' },
    )
    return response as unknown as Blob
  },
  getDiagram: async (projectId: number, diagramId: number): Promise<WritingDiagram> => {
    return api.get(`/writing/projects/${projectId}/diagrams/${diagramId}`) as unknown as WritingDiagram
  },
  fetchDiagramBlob: async (
    projectId: number,
    diagramId: number,
    contentSha256?: string,
  ): Promise<Blob> => {
    const response = await api.get<Blob>(
      `/writing/projects/${projectId}/diagrams/${diagramId}/png`,
      {
        responseType: 'blob',
        params: contentSha256 ? { v: contentSha256 } : undefined,
        headers: {
          'Cache-Control': 'no-cache',
          Pragma: 'no-cache',
        },
      },
    )
    return response as unknown as Blob
  },
  updateDiagram: async (
    projectId: number,
    diagramId: number,
    payload: WritingDiagramUpdatePayload,
  ): Promise<WritingDiagram> => {
    return api.put(
      `/writing/projects/${projectId}/diagrams/${diagramId}`,
      payload,
    ) as unknown as WritingDiagram
  },

  // 输出
  triggerRender: async (projectId: number) => {
    return api.post(`/writing/projects/${projectId}/render`, null, {
      timeout: 180000,
    })
  },
  getPreviewHtml: async (projectId: number): Promise<string> => {
    const response = await api.get<string>(`/writing/projects/${projectId}/preview`, {
      responseType: 'text',
      headers: {
        Accept: 'text/html',
      },
      timeout: 180000,
    })
    return response as unknown as string
  },
  getPreviewUrl: (projectId: number) => `/api/writing/projects/${projectId}/preview`,
  getDownloadUrl: (projectId: number) => `/api/writing/projects/${projectId}/download/docx`,
  downloadDocx: async (
    projectId: number,
    options: { allowBlockingIssues?: boolean } = {},
  ): Promise<Blob> => {
    const response = await api.get<Blob>(`/writing/projects/${projectId}/download/docx`, {
      params: options.allowBlockingIssues
        ? { allow_blocking_issues: true }
        : undefined,
      responseType: 'blob',
      timeout: 120000,
    })
    return response as unknown as Blob
  },
}
