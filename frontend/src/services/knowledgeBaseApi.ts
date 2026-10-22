import { api } from './httpClient'

export interface KnowledgeDocument {
  id: number
  title: string
  filename: string
  fileSize: number
  pageCount: number | null
  applicableFlows: string[]
  businessType: string
  businessLabel: string | null
  validFrom: string | null
  validTo: string | null
  source: string | null
  authority: string
  enabled: boolean
  version: string
  ownerScope: string
  industryTags: string[]
  regionTags: string[]
  reviewStatus: string
  processingStatus: string
  errorMessage: string | null
  processedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface KnowledgeAtom {
  id: number
  documentId: number
  title: string
  businessType: string
  businessLabel: string | null
  summary: string | null
  content: string
  pageStart: number | null
  pageEnd: number | null
  atomType: string
  confidence: number
  sourceChunkIds: number[]
  sourceImageIds: number[]
  processingStatus: string
  createdAt: string
  updatedAt: string
}

export interface KnowledgeProjectionResult {
  createdMaterialIds: number[]
  skippedDocumentIds: number[]
  warnings: string[]
}

export interface KnowledgeDocumentUpdatePayload {
  title?: string
  applicableFlows?: string[]
  businessType?: string
  businessLabel?: string | null
  validFrom?: string | null
  validTo?: string | null
  source?: string | null
  authority?: string
  enabled?: boolean
  version?: string
  ownerScope?: string
  industryTags?: string[]
  regionTags?: string[]
  reviewStatus?: string
  supersededById?: number | null
}

export interface KnowledgeUsageConfig {
  enabled: boolean
  flow: string | null
  selectedKbDocumentIds: number[]
  usagePolicy: string | null
  confirmedByUser: boolean
  confirmedAt: string | null
}

export interface KnowledgeUsageUpdatePayload {
  enabled: boolean
  flow: string | null
  selectedKbDocumentIds: number[]
  usagePolicy?: string | null
}

function transformKnowledgeUsageConfig(raw: Record<string, unknown>): KnowledgeUsageConfig {
  const ids = raw.selected_kb_document_ids
  const idsArray = Array.isArray(ids)
    ? (ids as unknown[])
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item))
    : []
  return {
    enabled: Boolean(raw.enabled),
    flow: (raw.flow as string) || null,
    selectedKbDocumentIds: idsArray,
    usagePolicy: (raw.usage_policy as string) || null,
    confirmedByUser: Boolean(raw.confirmed_by_user),
    confirmedAt: (raw.confirmed_at as string) || null,
  }
}

function unwrapApiData<T>(payload: unknown): T {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data
  }
  return payload as T
}

function transformKnowledgeDocument(raw: Record<string, unknown>): KnowledgeDocument {
  return {
    id: raw.id as number,
    title: raw.title as string,
    filename: raw.filename as string,
    fileSize: (raw.file_size as number) || 0,
    pageCount: (raw.page_count as number) ?? null,
    applicableFlows: Array.isArray(raw.applicable_flows) ? raw.applicable_flows as string[] : [],
    businessType: raw.business_type as string,
    businessLabel: (raw.business_label as string) || null,
    validFrom: (raw.valid_from as string) || null,
    validTo: (raw.valid_to as string) || null,
    source: (raw.source as string) || null,
    authority: (raw.authority as string) || 'user_experience',
    enabled: Boolean(raw.enabled),
    version: (raw.version as string) || '1',
    ownerScope: (raw.owner_scope as string) || 'personal',
    industryTags: Array.isArray(raw.industry_tags) ? raw.industry_tags as string[] : [],
    regionTags: Array.isArray(raw.region_tags) ? raw.region_tags as string[] : [],
    reviewStatus: (raw.review_status as string) || 'pending',
    processingStatus: (raw.processing_status as string) || 'saved',
    errorMessage: (raw.error_message as string) || null,
    processedAt: (raw.processed_at as string) || null,
    createdAt: raw.created_at as string,
    updatedAt: raw.updated_at as string,
  }
}

function transformKnowledgeAtom(raw: Record<string, unknown>): KnowledgeAtom {
  return {
    id: raw.id as number,
    documentId: raw.document_id as number,
    title: raw.title as string,
    businessType: raw.business_type as string,
    businessLabel: (raw.business_label as string) || null,
    summary: (raw.summary as string) || null,
    content: (raw.content as string) || '',
    pageStart: (raw.page_start as number) ?? null,
    pageEnd: (raw.page_end as number) ?? null,
    atomType: (raw.atom_type as string) || 'page',
    confidence: (raw.confidence as number) ?? 0,
    sourceChunkIds: Array.isArray(raw.source_chunk_ids) ? raw.source_chunk_ids as number[] : [],
    sourceImageIds: Array.isArray(raw.source_image_ids) ? raw.source_image_ids as number[] : [],
    processingStatus: (raw.processing_status as string) || 'completed',
    createdAt: raw.created_at as string,
    updatedAt: raw.updated_at as string,
  }
}

export const knowledgeBaseService = {
  listDocuments: async (params?: {
    applicableFlow?: string
    businessType?: string
    includeDisabled?: boolean
  }): Promise<KnowledgeDocument[]> => {
    const response = await api.get('/knowledge-base/documents', {
      params: {
        applicable_flow: params?.applicableFlow,
        business_type: params?.businessType,
        include_disabled: params?.includeDisabled,
      },
    })
    const raw = unwrapApiData<Record<string, unknown>[]>(response)
    return Array.isArray(raw) ? raw.map(transformKnowledgeDocument) : []
  },
  uploadDocument: async (payload: {
    file: File
    title: string
    businessType: string
    applicableFlows: string[]
    businessLabel?: string
    authority?: string
    source?: string
    version?: string
    validFrom?: string
    validTo?: string
    ownerScope?: string
    industryTags?: string[]
    regionTags?: string[]
    enabled?: boolean
  }): Promise<KnowledgeDocument> => {
    const form = new FormData()
    form.append('file', payload.file)
    form.append('title', payload.title)
    form.append('business_type', payload.businessType)
    form.append('applicable_flows', payload.applicableFlows.join(','))
    if (payload.businessLabel) form.append('business_label', payload.businessLabel)
    if (payload.authority) form.append('authority', payload.authority)
    if (payload.source) form.append('source', payload.source)
    if (payload.version) form.append('version', payload.version)
    if (payload.validFrom) form.append('valid_from', payload.validFrom)
    if (payload.validTo) form.append('valid_to', payload.validTo)
    if (payload.ownerScope) form.append('owner_scope', payload.ownerScope)
    if (payload.industryTags) form.append('industry_tags', payload.industryTags.join(','))
    if (payload.regionTags) form.append('region_tags', payload.regionTags.join(','))
    form.append('enabled', String(payload.enabled ?? true))
    const response = await api.post('/knowledge-base/documents', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000,
    })
    return transformKnowledgeDocument(unwrapApiData<Record<string, unknown>>(response))
  },
  processDocument: async (documentId: number): Promise<KnowledgeDocument> => {
    const response = await api.post(`/knowledge-base/documents/${documentId}/process`)
    return transformKnowledgeDocument(unwrapApiData<Record<string, unknown>>(response))
  },
  updateDocument: async (documentId: number, payload: KnowledgeDocumentUpdatePayload): Promise<KnowledgeDocument> => {
    const response = await api.put(`/knowledge-base/documents/${documentId}`, {
      title: payload.title,
      applicable_flows: payload.applicableFlows,
      business_type: payload.businessType,
      business_label: payload.businessLabel,
      valid_from: payload.validFrom,
      valid_to: payload.validTo,
      source: payload.source,
      authority: payload.authority,
      enabled: payload.enabled,
      version: payload.version,
      owner_scope: payload.ownerScope,
      industry_tags: payload.industryTags,
      region_tags: payload.regionTags,
      review_status: payload.reviewStatus,
      superseded_by_id: payload.supersededById,
    })
    return transformKnowledgeDocument(unwrapApiData<Record<string, unknown>>(response))
  },
  deleteDocument: async (documentId: number): Promise<void> => {
    await api.delete(`/knowledge-base/documents/${documentId}`)
  },
  listAtoms: async (documentId: number): Promise<KnowledgeAtom[]> => {
    const response = await api.get(`/knowledge-base/documents/${documentId}/atoms`)
    const raw = unwrapApiData<Record<string, unknown>[]>(response)
    return Array.isArray(raw) ? raw.map(transformKnowledgeAtom) : []
  },
  projectToWriting: async (projectId: number, kbDocumentIds: number[]): Promise<KnowledgeProjectionResult> => {
    const response = await api.post(`/knowledge-base/writing/projects/${projectId}/project`, {
      kb_document_ids: kbDocumentIds,
    })
    const raw = unwrapApiData<Record<string, unknown>>(response)
    return {
      createdMaterialIds: (raw.created_material_ids as number[]) || [],
      skippedDocumentIds: (raw.skipped_document_ids as number[]) || [],
      warnings: (raw.warnings as string[]) || [],
    }
  },
  getProjectUsageConfig: async (projectId: number): Promise<KnowledgeUsageConfig> => {
    const response = await api.get(`/knowledge-base/projects/${projectId}/usage-config`)
    const raw = unwrapApiData<Record<string, unknown>>(response)
    return transformKnowledgeUsageConfig(raw)
  },
  updateProjectUsageConfig: async (
    projectId: number,
    payload: KnowledgeUsageUpdatePayload,
  ): Promise<KnowledgeUsageConfig> => {
    const response = await api.put(`/knowledge-base/projects/${projectId}/usage-config`, {
      enabled: payload.enabled,
      flow: payload.flow,
      selected_kb_document_ids: payload.selectedKbDocumentIds,
      usage_policy: payload.usagePolicy,
    })
    const raw = unwrapApiData<Record<string, unknown>>(response)
    return transformKnowledgeUsageConfig(raw)
  },
  projectAtomsToWriting: async (projectId: number, kbAtomIds: number[]): Promise<KnowledgeProjectionResult> => {
    const response = await api.post(`/knowledge-base/writing/projects/${projectId}/project-atoms`, {
      kb_atom_ids: kbAtomIds,
    })
    const raw = unwrapApiData<Record<string, unknown>>(response)
    return {
      createdMaterialIds: (raw.created_material_ids as number[]) || [],
      skippedDocumentIds: (raw.skipped_document_ids as number[]) || [],
      warnings: (raw.warnings as string[]) || [],
    }
  },
}
