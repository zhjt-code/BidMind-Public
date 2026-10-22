/**
 * P0-C 模型配置 Modal 壳
 *
 * 职责: 三 Tab 组织(连接/能力与限制/生成约束)、跨 Tab 校验定位、
 *       保存与「保存并验证」(能力探测)请求编排
 * 不做: 不承担调度池/优先级操作(归 Settings 页卡片)
 *
 * 宽度 680px: 能力状态行含"标签+探测徽标+说明+三态选择", 512px 会换行拥挤。
 */

import React, { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Modal } from '@/components'
import {
  settingsService,
  type DeclaredCapabilitiesPayload,
  type ConfigSavePayload,
  type ModelConfigData,
  type ModelPurpose,
  type ApiKeyCreateItem,
} from '@/services/settingsApi'
import ConnectionTab from './ConnectionTab'
import CapabilityTab from './CapabilityTab'
import GenerationTab from './GenerationTab'
import {
  MASKED_KEY,
  PURPOSE_TAB_LABELS,
  emptyForm,
  formFromConfig,
  validateForm,
  type ModelConfigForm,
  type TabKey,
} from './types'

interface Props {
  visible: boolean
  purpose: ModelPurpose
  /** null=新增, 否则编辑该配置 */
  config: ModelConfigData | null
  onSaved: () => void
  onCancel: () => void
}

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'connection', label: '连接' },
  { key: 'capability', label: '能力与限制' },
  { key: 'generation', label: '生成约束' },
]

function parseIntOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = parseInt(trimmed, 10)
  return Number.isNaN(parsed) ? null : parsed
}

function parseFloatOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = parseFloat(trimmed)
  return Number.isNaN(parsed) ? null : parsed
}

const ModelConfigModal: React.FC<Props> = ({ visible, purpose, config, onSaved, onCancel }) => {
  const [tab, setTab] = useState<TabKey>('connection')
  const [form, setForm] = useState<ModelConfigForm>(emptyForm())
  const [errors, setErrors] = useState<Partial<Record<TabKey, string[]>>>({})
  const [saving, setSaving] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)

  useEffect(() => {
    if (visible) {
      setForm(config ? formFromConfig(config) : emptyForm())
      setTab('connection')
      setErrors({})
      setRequestError(null)
    }
  }, [visible, config])

  const busy = saving || verifying

  const buildCapabilitiesPayload = (): DeclaredCapabilitiesPayload => ({
    context_window_tokens: parseIntOrNull(form.context_window_tokens),
    max_output_tokens: parseIntOrNull(form.max_output_tokens),
    thinking_policy: form.thinking_policy,
    reasoning_effort: form.reasoning_effort,
    supports_vision: form.supports_vision,
    supports_seed: form.supports_seed,
    json_object: form.json_object,
    json_schema: form.json_schema,
    strict_schema: form.strict_schema,
  })

  const doSave = async (): Promise<string | null> => {
    const validation = validateForm(form)
    setErrors(validation)
    if (Object.keys(validation).length > 0) {
      const firstTab = TABS.find((t) => validation[t.key]?.length)
      if (firstTab) setTab(firstTab.key)
      return null
    }
    setRequestError(null)
    const capabilities = buildCapabilitiesPayload()
    const parsedTemp = parseFloatOrNull(form.temperature)
    const base: ConfigSavePayload = {
      temperature_mode: form.temperature_mode,
      capabilities,
    }
    if (parsedTemp !== null) base.temperature = parsedTemp
    if (form.temperature_mode === 'exact') {
      const exact = parseFloatOrNull(form.temperature_exact)
      if (exact !== null) base.temperature_exact = exact
    }
    if (form.temperature_mode === 'clamp') {
      const min = parseFloatOrNull(form.temperature_min)
      const max = parseFloatOrNull(form.temperature_max)
      if (min !== null) base.temperature_min = min
      if (max !== null) base.temperature_max = max
    }

    try {
      if (config) {
        const payload: ConfigSavePayload = { ...base }
        if (form.api_base) payload.api_base = form.api_base
        if (form.api_key && form.api_key !== MASKED_KEY) payload.api_key = form.api_key
        if (form.model_name) payload.model_name = form.model_name
        payload.api_keys = form.api_keys.map((k): ApiKeyCreateItem => ({
          key: k.key === MASKED_KEY ? '' : k.key,
          label: k.label,
          enabled: k.enabled,
        }))
        await settingsService.updateConfig(purpose, config.id, payload)
        return config.id
      }
      const created = await settingsService.addConfig(purpose, {
        ...base,
        api_base: form.api_base,
        api_key: form.api_key,
        model_name: form.model_name,
        api_keys: form.api_keys
          .filter((k) => k.key && k.key !== MASKED_KEY)
          .map((k): ApiKeyCreateItem => ({ key: k.key, label: k.label, enabled: k.enabled })),
      })
      return created.id
    } catch (err) {
      setRequestError(`保存失败: ${err instanceof Error ? err.message : '未知错误'}`)
      return null
    }
  }

  const handleSaveOnly = async () => {
    setSaving(true)
    try {
      const savedId = await doSave()
      if (savedId) {
        toast.success('配置已保存（未验证，加入调度池前建议先验证能力）')
        onSaved()
      }
    } finally {
      setSaving(false)
    }
  }

  const handleSaveAndVerify = async () => {
    setSaving(true)
    let savedId: string | null = null
    try {
      savedId = await doSave()
    } finally {
      setSaving(false)
    }
    if (!savedId) return

    setVerifying(true)
    try {
      const resp = await settingsService.verifyConfig(purpose, savedId)
      const data = resp.data
      if (data.all_ok) {
        toast.success(`能力验证通过（${Object.keys(data.reports).length} 项探测）`)
      } else {
        const failed = Object.values(data.reports).filter((r) => !r.ok)
        toast.warning(
          `部分能力验证失败: ${failed.map((r) => `${r.kind}: ${r.error || '失败'}`).join('; ')}`,
          { duration: 8000 },
        )
      }
      onSaved()
    } catch (err) {
      toast.error(`能力验证失败: ${err instanceof Error ? err.message : '未知错误'}`)
      onSaved()
    } finally {
      setVerifying(false)
    }
  }

  const allErrors = TABS.flatMap((t) => (errors[t.key] || []).map((msg) => ({ tab: t, msg })))

  return (
    <Modal
      visible={visible}
      title={
        config
          ? `编辑模型配置 · ${PURPOSE_TAB_LABELS[purpose]}`
          : `添加模型配置 · ${PURPOSE_TAB_LABELS[purpose]}`
      }
      onClose={onCancel}
      maxWidth="max-w-[680px]"
      maskClosable={!busy}
      footer={
        <>
          <button onClick={onCancel} disabled={busy}
            className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50">
            取消
          </button>
          <button onClick={handleSaveOnly} disabled={busy}
            className="px-4 py-2 text-sm text-primary border border-primary rounded-lg hover:bg-blue-50 disabled:opacity-50">
            {saving && !verifying ? '保存中...' : '仅保存'}
          </button>
          <button onClick={handleSaveAndVerify} disabled={busy}
            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 disabled:bg-gray-300">
            {verifying ? '验证能力中...' : saving ? '保存中...' : '保存并验证'}
          </button>
        </>
      }
    >
      {/* Tab 头 */}
      <div className="flex border-b border-gray-200 -mt-1 mb-4">
        {TABS.map((t) => {
          const errCount = errors[t.key]?.length || 0
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
                tab === t.key
                  ? 'border-primary text-primary font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}>
              {t.label}
              {errCount > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-100 px-1 text-[10px] font-medium text-rose-700">
                  {errCount}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {requestError && (
        <div className="mb-4 p-3 bg-red-50 rounded-lg border border-red-200">
          <p className="text-sm text-red-800">{requestError}</p>
        </div>
      )}

      <div className="min-h-[320px]">
        {tab === 'connection' && <ConnectionTab form={form} onChange={setForm} />}
        {tab === 'capability' && (
          <CapabilityTab purpose={purpose} form={form}
            capabilities={config?.capabilities} onChange={setForm} />
        )}
        {tab === 'generation' && (
          <GenerationTab purpose={purpose} form={form} onChange={setForm} />
        )}
      </div>

      {/* 全局错误摘要(紧贴 footer, 切换 Tab 不消失) */}
      {allErrors.length > 0 && (
        <div className="mt-4 p-3 bg-rose-50 border border-rose-200 rounded-lg">
          <p className="text-xs font-medium text-rose-700 mb-1">请修正以下 {allErrors.length} 项问题:</p>
          <ul className="space-y-0.5">
            {allErrors.map(({ tab: errTab, msg }, i) => (
              <li key={`${errTab.key}-${i}`}>
                <button onClick={() => setTab(errTab.key)}
                  className="text-xs text-rose-600 hover:underline text-left">
                  [{errTab.label}] {msg}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  )
}

export default ModelConfigModal

