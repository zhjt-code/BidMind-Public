import { api } from './httpClient'

export interface LlmFlowSummary {
  flow_key: string
  name: string
  description: string | null
  route_prefix: string | null
  display_order: number
  is_enabled: boolean
  stage_count: number
  node_count: number
}

export type LlmNodeClass = 'mechanical' | 'judgment'

export interface LlmNodeSummary {
  node_key: string
  name: string
  description: string | null
  visibility: string
  editable_level: string
  execution_channel: string
  node_class: LlmNodeClass
  call_method: string
  default_temperature: number
  default_max_tokens: number
  active_version: number | null
  total_versions: number
  param_count: Record<string, number>
  has_code_update: boolean
  has_draft: boolean
  last_published_at: string | null
  recent_error_rate: number | null
}

export interface LlmStageInfo {
  stage_key: string
  name: string
  description: string | null
  display_order: number
  nodes: LlmNodeSummary[]
}

export interface LlmEdgeInfo {
  from_node_key: string
  to_node_key: string
  edge_type: string
  param_mapping: Record<string, string> | null
  description: string | null
}

export interface LlmFlowTopology {
  flow_key: string
  name: string
  description: string | null
  stages: LlmStageInfo[]
  edges: LlmEdgeInfo[]
}

export interface LlmTemplateVersion {
  id: number
  template_role: string
  version: number
  content: string
  is_active: boolean
  source: string
  status: string
  change_note: string | null
  created_by: string | null
  validated_at: string | null
  published_at: string | null
  created_at: string
}

export interface LlmParamDef {
  id: number
  param_key: string
  direction: string
  data_type: string
  is_required: boolean
  default_value: string | null
  description: string | null
  source_type: string
  source_hint: string | null
  example_value: string | null
  ui_hint: string
  status: string
}

export interface LlmNodeDetail {
  node_key: string
  name: string
  description: string | null
  builder_key: string | null
  resolver_key: string | null
  source_file: string | null
  execution_channel: string
  call_method: string
  default_temperature: number
  default_max_tokens: number
  output_schema_name: string | null
  visibility: string
  editable_level: string
  supports_hot_reload: boolean
  active_prompt: LlmTemplateVersion | null
  all_versions: LlmTemplateVersion[]
  params: LlmParamDef[]
}

export interface LlmRegistryStatus {
  total_flows: number
  total_stages: number
  total_nodes: number
  total_active_versions: number
  code_update_warnings: number
  last_sync_at: string | null
}

export interface BatchScopeRequest {
  scope: 'flow' | 'all_pending'
  flow_key?: string
  include_technical?: boolean
  change_note?: string
}

export interface BatchPublishPassedRequest {
  node_keys: string[]
  change_note?: string
}

export interface BatchRunItemCheck {
  check_type: string
  passed: boolean
  message: string
  details?: Record<string, unknown> | null
}

export interface BatchRunItem {
  node_key: string
  node_name: string
  flow_key: string
  flow_name: string
  stage_key: string
  stage_name: string
  path_text: string
  status: string
  checks: BatchRunItemCheck[]
  downstream_nodes: string[]
  error_message?: string | null
}

export interface BatchRunSummary {
  total: number
  validated_passed: number
  validated_failed: number
  published: number
  skipped: number
}

export interface BatchRunResponse {
  scope: string
  flow_key?: string | null
  summary: BatchRunSummary
  can_publish_all: boolean
  can_publish_passed: boolean
  items: BatchRunItem[]
}

export const llmCenterService = {
  listFlows: async (): Promise<LlmFlowSummary[]> => {
    const response = await api.get('/admin/llm-center/flows')
    return response as unknown as LlmFlowSummary[]
  },

  getFlowTopology: async (flowKey: string): Promise<LlmFlowTopology> => {
    const response = await api.get(`/admin/llm-center/flows/${flowKey}`)
    return response as unknown as LlmFlowTopology
  },

  getNodeDetail: async (nodeKey: string): Promise<LlmNodeDetail> => {
    const response = await api.get(`/admin/llm-center/nodes/${nodeKey}`)
    return response as unknown as LlmNodeDetail
  },

  getNodeVersions: async (nodeKey: string): Promise<LlmTemplateVersion[]> => {
    const response = await api.get(`/admin/llm-center/nodes/${nodeKey}/versions`)
    return response as unknown as LlmTemplateVersion[]
  },

  getNodeParams: async (nodeKey: string): Promise<LlmParamDef[]> => {
    const response = await api.get(`/admin/llm-center/nodes/${nodeKey}/params`)
    return response as unknown as LlmParamDef[]
  },

  getRegistryStatus: async (): Promise<LlmRegistryStatus> => {
    const response = await api.get('/admin/llm-center/registry/status')
    return response as unknown as LlmRegistryStatus
  },

  discardDraft: async (nodeKey: string): Promise<{ discarded: number }> => {
    const response = await api.delete(`/admin/llm-center/nodes/${nodeKey}/draft`)
    return response as unknown as { discarded: number }
  },

  createDraft: async (nodeKey: string, data: { template_role: string; content: string; change_note?: string }): Promise<LlmTemplateVersion> => {
    const response = await api.post(`/admin/llm-center/nodes/${nodeKey}/draft`, data)
    return response as unknown as LlmTemplateVersion
  },

  validateDraft: async (nodeKey: string): Promise<{ passed: boolean; checks: { check_type: string; passed: boolean; message: string }[] }> => {
    const response = await api.post(`/admin/llm-center/nodes/${nodeKey}/validate`)
    return response as unknown as { passed: boolean; checks: { check_type: string; passed: boolean; message: string }[] }
  },

  publishVersion: async (nodeKey: string, changeNote?: string): Promise<LlmTemplateVersion> => {
    const response = await api.post(`/admin/llm-center/nodes/${nodeKey}/publish`, { change_note: changeNote })
    return response as unknown as LlmTemplateVersion
  },

  rollbackVersion: async (nodeKey: string): Promise<LlmTemplateVersion> => {
    const response = await api.post(`/admin/llm-center/nodes/${nodeKey}/rollback`)
    return response as unknown as LlmTemplateVersion
  },

  updateNodeConfig: async (
    nodeKey: string,
    data: {
      default_temperature?: number
      default_max_tokens?: number
      node_class?: LlmNodeClass
    },
  ): Promise<void> => {
    await api.put(`/admin/llm-center/nodes/${nodeKey}/config`, data)
  },

  getPublishHistory: async (nodeKey: string): Promise<{ id: number; action: string; published_by: string; template_version_id: number; change_note: string | null; created_at: string }[]> => {
    const response = await api.get(`/admin/llm-center/nodes/${nodeKey}/publish-history`)
    return response as unknown as { id: number; action: string; published_by: string; template_version_id: number; change_note: string | null; created_at: string }[]
  },

  getImpactAnalysis: async (nodeKey: string): Promise<{ affected_downstream_nodes: string[]; running_projects_count: number; risk_level: string; recommendation: string }> => {
    const response = await api.post(`/admin/llm-center/nodes/${nodeKey}/impact-analysis`)
    return response as unknown as { affected_downstream_nodes: string[]; running_projects_count: number; risk_level: string; recommendation: string }
  },

  getExecutionTraces: async (nodeKey?: string): Promise<{ id: number; node_key: string; project_id: number | null; call_count: number; total_latency_ms: number; error_count: number; model_name: string | null; created_at: string }[]> => {
    const params = nodeKey ? `?node_key=${nodeKey}` : ''
    const response = await api.get(`/admin/llm-center/executions${params}`)
    return response as unknown as { id: number; node_key: string; project_id: number | null; call_count: number; total_latency_ms: number; error_count: number; model_name: string | null; created_at: string }[]
  },

  batchValidate: async (data: BatchScopeRequest): Promise<BatchRunResponse> => {
    const response = await api.post('/admin/llm-center/batch/validate', data)
    return response as unknown as BatchRunResponse
  },

  batchValidateAndPublish: async (data: BatchScopeRequest): Promise<BatchRunResponse> => {
    const response = await api.post('/admin/llm-center/batch/validate-publish', data)
    return response as unknown as BatchRunResponse
  },

  batchPublishPassed: async (data: BatchPublishPassedRequest): Promise<BatchRunResponse> => {
    const response = await api.post('/admin/llm-center/batch/publish-passed', data)
    return response as unknown as BatchRunResponse
  },
}
