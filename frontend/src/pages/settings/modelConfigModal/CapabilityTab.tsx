/**
 * P0-C 模型配置 Modal — 能力与限制 Tab
 *
 * 职责: 上下文/输出上限编辑 + 用途化能力状态展示(探测只读) + 手动覆盖(默认收起)
 * 不做: 不执行探测(由 footer「保存并验证」触发)、不发请求
 *
 * 显示规则:
 * - vision: 隐藏 JSON 三项, 突出图像输入验证状态;
 * - review: 显示 json_object/json_schema, strict 收入手动覆盖;
 * - extract: 三项全部显示且 strict 高亮(CoVe/Judge 复用本池，依赖 strict schema)；
 * - judge: 兼容旧用途，展示规则与 extract 相同。
 */

import React, { useState } from 'react'
import type { CapabilitySummary, ModelPurpose, TriState } from '@/services/settingsApi'
import type { ModelConfigForm } from './types'

interface Props {
  purpose: ModelPurpose
  form: ModelConfigForm
  capabilities?: CapabilitySummary | null
  onChange: (form: ModelConfigForm) => void
}

const TRISTATE_LABELS: Record<TriState, string> = {
  auto: '自动检测',
  supported: '支持',
  unsupported: '不支持',
}

const CAPABILITY_LABELS: Record<string, { label: string; hint: string }> = {
  supports_vision: { label: '图像输入', hint: '视觉池必需；由真实图片探测验证' },
  supports_seed: { label: 'Seed 参数', hint: '声明不支持时请求将省略 seed，避免 400' },
  json_object: { label: 'JSON Object', hint: '结构化提取/审查的基础能力' },
  json_schema: { label: 'JSON Schema', hint: '按 Schema 约束输出结构' },
  strict_schema: { label: 'Strict Schema', hint: '提取池上的 CoVe/Judge 复审与评分候选依赖的严格约束' },
}

function probeBadge(entry?: { effective: TriState; observed: { ok: boolean; probed_at: string } | null; source: string }) {
  if (!entry) return null
  if (entry.observed) {
    return entry.observed.ok ? (
      <span className="text-[11px] px-1.5 py-0.5 rounded bg-green-100 text-green-700">已验证</span>
    ) : (
      <span className="text-[11px] px-1.5 py-0.5 rounded bg-red-100 text-red-700">验证失败</span>
    )
  }
  if (entry.effective === 'unsupported') {
    return <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-600">已禁用</span>
  }
  return <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">未验证</span>
}

const CapabilityTab: React.FC<Props> = ({ purpose, form, capabilities, onChange }) => {
  const [showOverride, setShowOverride] = useState(false)

  const highlightStrict = purpose === 'extract' || purpose === 'judge'
  const primaryFields: string[] =
    purpose === 'vision'
      ? ['supports_vision']
      : highlightStrict
        ? ['json_object', 'json_schema', 'strict_schema']
        : ['json_object', 'json_schema']
  const overrideFields: string[] = [
    ...(purpose === 'vision' ? [] : ['supports_vision']),
    'supports_seed',
    ...(purpose === 'review' ? ['strict_schema'] : []),
  ]

  const setTristate = (field: string, value: TriState) => {
    onChange({ ...form, [field]: value } as ModelConfigForm)
  }

  const renderRow = (field: string, highlight = false) => {
    const meta = CAPABILITY_LABELS[field]
    const entry = capabilities?.capabilities?.[field]
    const declared = (form as unknown as Record<string, TriState>)[field] ?? 'auto'
    return (
      <div key={field}
        className={`flex items-center justify-between gap-3 px-3 py-2 rounded-lg border ${
          highlight ? 'border-amber-200 bg-amber-50/50' : 'border-gray-100 bg-gray-50/60'
        }`}>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-800">{meta.label}</span>
            {probeBadge(entry)}
          </div>
          <p className="text-xs text-gray-400 mt-0.5">{meta.hint}</p>
          {entry?.observed && !entry.observed.ok && entry.observed.evidence && (
            <p className="text-xs text-red-500 mt-0.5 truncate" title={entry.observed.evidence}>
              {entry.observed.evidence}
            </p>
          )}
        </div>
        <select value={declared}
          onChange={(e) => setTristate(field, e.target.value as TriState)}
          className="text-xs border rounded px-2 py-1 bg-white text-gray-700 shrink-0">
          {(Object.keys(TRISTATE_LABELS) as TriState[]).map((v) => (
            <option key={v} value={v}>{TRISTATE_LABELS[v]}</option>
          ))}
        </select>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* 数值上限 */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">上下文窗口 (Token)</label>
          <input type="number" min={1024} step={1024} value={form.context_window_tokens}
            onChange={(e) => onChange({ ...form, context_window_tokens: e.target.value })}
            placeholder="留空不限制"
            className="w-full border rounded-lg px-3 py-2 focus:outline-hidden focus:ring-2 focus:ring-primary" />
          <p className="text-xs text-gray-400 mt-1">按厂商文档填写；决定长文档任务能否路由到本模型</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">最大输出 (Token)</label>
          <input type="number" min={256} step={256} value={form.max_output_tokens}
            onChange={(e) => onChange({ ...form, max_output_tokens: e.target.value })}
            placeholder="留空不限制"
            className="w-full border rounded-lg px-3 py-2 focus:outline-hidden focus:ring-2 focus:ring-primary" />
          <p className="text-xs text-gray-400 mt-1">低于任务输出需求的模型将被调度自动跳过，避免截断</p>
        </div>
      </div>

      {/* 用途能力状态 */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-700">能力状态</span>
          {capabilities?.observed_at && (
            <span className="text-[11px] text-gray-400">
              最近验证 {new Date(capabilities.observed_at).toLocaleString('zh-CN')}
            </span>
          )}
        </div>
        <div className="space-y-2">
          {primaryFields.map((f) => renderRow(f, highlightStrict && f === 'strict_schema'))}
        </div>
        <p className="text-xs text-gray-400 mt-2">
          「自动检测」由系统按运行时探测与降级学习决定；仅当厂商文档明确时才手动改为支持/不支持。
        </p>
      </div>

      {/* 手动覆盖(收起) */}
      {overrideFields.length > 0 && (
        <div>
          <button type="button" onClick={() => setShowOverride(!showOverride)}
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1">
            <svg className={`w-3 h-3 transition-transform ${showOverride ? 'rotate-90' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            高级能力覆盖（{overrideFields.length} 项）
          </button>
          {showOverride && (
            <div className="space-y-2 mt-2">
              {overrideFields.map((f) => renderRow(f))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CapabilityTab

