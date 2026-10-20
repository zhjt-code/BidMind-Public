import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

export interface ApiKeyInfo {
  label: string
  enabled: boolean
  key_preview: string
}

export interface ApiKeyCreateItem {
  key: string
  label: string
  enabled: boolean
}

export type TriState = 'auto' | 'supported' | 'unsupported'
export type ThinkingPolicy = 'auto' | 'disabled' | 'enabled'
export type ReasoningEffort = 'default' | 'low' | 'medium' | 'high' | 'max'
export type TemperatureMode = 'free' | 'exact' | 'clamp'

export interface CapabilityObserved {
  ok: boolean
  probed_at: string
  evidence: string
}

export interface CapabilityEntry {
  declared: TriState
  observed: CapabilityObserved | null
  effective: TriState
  source: 'default' | 'declared' | 'observed'
}

export interface CapabilitySummary {
  context_window_tokens: number | null
  max_output_tokens: number | null
  thinking_policy: ThinkingPolicy
  reasoning_effort: ReasoningEffort
  capabilities: Record<string, CapabilityEntry>
  observed_at: string | null
}

/** P0-C: 保存时下发的能力声明(observed 由后端探测专管) */
export interface DeclaredCapabilitiesPayload {
  context_window_tokens?: number | null
  max_output_tokens?: number | null
  thinking_policy?: ThinkingPolicy
  reasoning_effort?: ReasoningEffort
  supports_vision?: TriState
  supports_seed?: TriState
  json_object?: TriState
  json_schema?: TriState
  strict_schema?: TriState
}

export interface ProbeReportData {
  kind: string
  ok: boolean
  latency_ms: number
  error: string | null
  probed_at: string
}

export interface VerifyConfigResult {
  config_id: string
  all_ok: boolean
  reports: Record<string, ProbeReportData>
  capabilities: CapabilitySummary
}

export interface ModelConfigData {
  id: string
  api_base: string
  model_name: string
  config_type: 'local' | 'cloud'
  api_key_configured: boolean
  api_key_preview?: string
  key_count: number
  api_keys_info: ApiKeyInfo[]
  temperature: number | null
  temperature_mode?: TemperatureMode
  temperature_exact?: number | null
  temperature_min?: number | null
  temperature_max?: number | null
  capabilities?: CapabilitySummary | null
}

export interface ConfigSavePayload {
  api_base?: string
  api_key?: string
  model_name?: string
  api_keys?: ApiKeyCreateItem[]
  temperature?: number | null
  temperature_mode?: TemperatureMode
  temperature_exact?: number | null
  temperature_min?: number | null
  temperature_max?: number | null
  capabilities?: DeclaredCapabilitiesPayload
}

export type ModelPurpose = 'vision' | 'review' | 'judge' | 'extract' | 'scoring'

export interface AISettingsData {
  vision_model_id: string
  review_model_id: string
  judge_model_id: string
  extract_model_id: string
  scoring_model_id: string
  vision_configs: ModelConfigData[]
  review_configs: ModelConfigData[]
  judge_configs: ModelConfigData[]
  extract_configs: ModelConfigData[]
  scoring_configs: ModelConfigData[]
  vision_pool_ids: string[]
  review_pool_ids: string[]
  judge_pool_ids: string[]
  extract_pool_ids: string[]
  scoring_pool_ids: string[]
  vision_pool_priority: Record<string, number>
  review_pool_priority: Record<string, number>
  judge_pool_priority: Record<string, number>
  extract_pool_priority: Record<string, number>
  scoring_pool_priority: Record<string, number>
}

interface KeyTestResult {
  label: string
  status: string
  error?: string
}

interface TestConnectionResult {
  config_id: string | null
  status: string
  error?: string
  results?: KeyTestResult[]
}

export const settingsService = {
  getAISettings: async (): Promise<AISettingsData> => {
    const response = await api.get<ApiResponse<AISettingsData>>('/settings/ai')
    return response.data
  },

  setModel: async (purpose: ModelPurpose, modelId: string) => {
    return api.put<ApiResponse<{ previous: string; current: string }>>(`/settings/ai/${purpose}/model`, { model_id: modelId })
  },

  setPool: async (purpose: ModelPurpose, poolIds: string[]) => {
    return api.put<ApiResponse<{ pool_ids: string[] }>>(`/settings/ai/${purpose}/pool`, { pool_ids: poolIds })
  },

  setPoolPriority: async (purpose: ModelPurpose, priority: Record<string, number>) => {
    return api.put<ApiResponse<{ priority: Record<string, number> }>>(`/settings/ai/${purpose}/pool-priority`, { priority })
  },

  addConfig: async (purpose: ModelPurpose, config: ConfigSavePayload & { api_base: string; api_key: string; model_name: string }) => {
    const response = await api.post<ApiResponse<ModelConfigData>>(`/settings/ai/${purpose}/configs`, config)
    return response.data
  },

  updateConfig: async (purpose: ModelPurpose, configId: string, config: ConfigSavePayload) => {
    const response = await api.put<ApiResponse<ModelConfigData>>(`/settings/ai/${purpose}/configs/${configId}`, config)
    return response.data
  },

  /** P0-C: 保存并验证——执行完整能力探测套件并写入 observed */
  verifyConfig: async (purpose: ModelPurpose, configId: string) => {
    const response = await api.post<ApiResponse<VerifyConfigResult>>(
      `/settings/ai/${purpose}/configs/${configId}/verify`, null, { timeout: 150000 },
    )
    return response
  },

  deleteConfig: async (purpose: ModelPurpose, configId: string) => {
    return api.delete<ApiResponse<{ deleted_id: string; current_model_id: string }>>(`/settings/ai/${purpose}/configs/${configId}`)
  },

  duplicateConfig: async (
    targetPurpose: ModelPurpose,
    body: { source_purpose: ModelPurpose; source_config_id: string },
  ) => {
    const response = await api.post<ApiResponse<ModelConfigData>>(
      `/settings/ai/${targetPurpose}/configs/duplicate`,
      body,
    )
    return response.data
  },

  testConnection: async (purpose?: ModelPurpose, configId?: string) => {
    const params = new URLSearchParams()
    if (configId) params.append('config_id', configId)
    if (purpose) params.append('purpose', purpose)
    const query = params.toString()
    return api.post<ApiResponse<TestConnectionResult>>(`/settings/ai/test${query ? `?${query}` : ''}`, null, {
      timeout: 65000,
    })
  },
}

