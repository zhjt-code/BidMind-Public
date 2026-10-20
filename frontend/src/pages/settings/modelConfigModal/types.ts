/**
 * P0-C 模型配置 Modal 共享类型
 *
 * 职责: 定义三 Tab 表单状态与校验类型
 * 不做: 不发请求(归 ModelConfigModal)、不定义能力语义(归后端 ai_capabilities)
 */

import type {
  ModelConfigData,
  ModelPurpose,
  ReasoningEffort,
  TemperatureMode,
  ThinkingPolicy,
  TriState,
} from '@/services/settingsApi'

export const MASKED_KEY = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022'

export type TabKey = 'connection' | 'capability' | 'generation'

export interface ApiKeyRow {
  _uid: string
  key: string
  label: string
  enabled: boolean
  _isExisting?: boolean
}

export interface ModelConfigForm {
  // ---- 连接 ----
  api_base: string
  api_key: string
  model_name: string
  api_keys: ApiKeyRow[]
  _hasExistingKey?: boolean
  // ---- 能力与限制 ----
  context_window_tokens: string
  max_output_tokens: string
  supports_vision: TriState
  supports_seed: TriState
  json_object: TriState
  json_schema: TriState
  strict_schema: TriState
  // ---- 生成约束 ----
  thinking_policy: ThinkingPolicy
  reasoning_effort: ReasoningEffort
  temperature: string
  temperature_mode: TemperatureMode
  temperature_exact: string
  temperature_min: string
  temperature_max: string
}

let _uidSeq = 0
export function nextApiKeyUid(): string {
  return `ak_${Date.now()}_${++_uidSeq}`
}

export function emptyForm(): ModelConfigForm {
  return {
    api_base: '',
    api_key: '',
    model_name: '',
    api_keys: [],
    _hasExistingKey: false,
    context_window_tokens: '',
    max_output_tokens: '',
    supports_vision: 'auto',
    supports_seed: 'auto',
    json_object: 'auto',
    json_schema: 'auto',
    strict_schema: 'auto',
    thinking_policy: 'auto',
    reasoning_effort: 'default',
    temperature: '',
    temperature_mode: 'free',
    temperature_exact: '',
    temperature_min: '',
    temperature_max: '',
  }
}

export function formFromConfig(config: ModelConfigData): ModelConfigForm {
  const caps = config.capabilities
  const capEntry = (field: string): TriState =>
    (caps?.capabilities?.[field]?.declared as TriState) ?? 'auto'
  return {
    api_base: config.api_base,
    api_key: config.api_key_configured ? MASKED_KEY : '',
    model_name: config.model_name,
    api_keys: (config.api_keys_info || []).map((ki) => ({
      _uid: nextApiKeyUid(),
      key: MASKED_KEY,
      label: ki.label,
      enabled: ki.enabled,
      _isExisting: true,
    })),
    _hasExistingKey: config.api_key_configured,
    context_window_tokens: caps?.context_window_tokens != null ? String(caps.context_window_tokens) : '',
    max_output_tokens: caps?.max_output_tokens != null ? String(caps.max_output_tokens) : '',
    supports_vision: capEntry('supports_vision'),
    supports_seed: capEntry('supports_seed'),
    json_object: capEntry('json_object'),
    json_schema: capEntry('json_schema'),
    strict_schema: capEntry('strict_schema'),
    thinking_policy: caps?.thinking_policy ?? 'auto',
    reasoning_effort: caps?.reasoning_effort ?? 'default',
    temperature: config.temperature != null ? String(config.temperature) : '',
    temperature_mode: config.temperature_mode ?? 'free',
    temperature_exact: config.temperature_exact != null ? String(config.temperature_exact) : '',
    temperature_min: config.temperature_min != null ? String(config.temperature_min) : '',
    temperature_max: config.temperature_max != null ? String(config.temperature_max) : '',
  }
}

/** 逐 Tab 校验, 返回 {tab: 错误消息列表} */
export function validateForm(
  form: ModelConfigForm,
): Partial<Record<TabKey, string[]>> {
  const errors: Partial<Record<TabKey, string[]>> = {}
  const connection: string[] = []
  if (!form.api_base.trim()) connection.push('请填写 API 基础 URL')
  if (!form.model_name.trim()) connection.push('请填写模型名称')
  if (connection.length) errors.connection = connection

  const capability: string[] = []
  const ctx = form.context_window_tokens.trim()
  const out = form.max_output_tokens.trim()
  if (ctx && (!/^\d+$/.test(ctx) || parseInt(ctx, 10) < 1024)) {
    capability.push('上下文窗口须为不小于 1024 的整数')
  }
  if (out && (!/^\d+$/.test(out) || parseInt(out, 10) < 256)) {
    capability.push('最大输出须为不小于 256 的整数')
  }
  if (ctx && out && /^\d+$/.test(ctx) && /^\d+$/.test(out)
      && parseInt(out, 10) > parseInt(ctx, 10)) {
    capability.push('最大输出不能超过上下文窗口')
  }
  if (capability.length) errors.capability = capability

  const generation: string[] = []
  if (form.temperature_mode === 'exact' && form.temperature_exact.trim() === '') {
    generation.push('固定温度模式必须填写固定值')
  }
  if (form.temperature_mode === 'clamp') {
    const min = parseFloat(form.temperature_min)
    const max = parseFloat(form.temperature_max)
    if (form.temperature_min.trim() === '' && form.temperature_max.trim() === '') {
      generation.push('区间温度模式至少填写最小或最大值')
    } else if (!Number.isNaN(min) && !Number.isNaN(max) && min > max) {
      generation.push('温度区间最小值不能大于最大值')
    }
  }
  if (generation.length) errors.generation = generation

  return errors
}

export const PURPOSE_TAB_LABELS: Record<ModelPurpose, string> = {
  vision: '视觉模型',
  review: '审查模型',
  judge: '复审模型',
  extract: '提取模型',
  scoring: '评分模型',
}

