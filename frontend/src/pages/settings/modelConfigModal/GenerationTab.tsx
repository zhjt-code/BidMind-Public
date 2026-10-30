/**
 * P0-C 模型配置 Modal — 生成约束 Tab
 *
 * 职责: 思考策略/推理强度 + 温度约束(free/exact/clamp 互斥置灰, 补齐既有 UI 缺口)
 * 不做: 不发请求、不承担业务节点温度(节点意图归 LLM Center)
 */

import React from 'react'
import type { ModelPurpose, ReasoningEffort, TemperatureMode, ThinkingPolicy } from '@/services/settingsApi'
import type { ModelConfigForm } from './types'

interface Props {
  purpose: ModelPurpose
  form: ModelConfigForm
  onChange: (form: ModelConfigForm) => void
}

const THINKING_OPTIONS: Array<{ value: ThinkingPolicy; label: string; hint: string }> = [
  { value: 'auto', label: '自动(服务端默认)', hint: '不发送思考参数，保持厂商默认行为' },
  { value: 'disabled', label: '关闭思考', hint: '事实抽取/结构化任务推荐；降低延迟与 token 消耗' },
  { value: 'enabled', label: '开启思考', hint: '思考 token 会占用输出预算，需保证最大输出充足' },
]

const EFFORT_OPTIONS: Array<{ value: ReasoningEffort; label: string }> = [
  { value: 'default', label: '默认(不发送)' },
  { value: 'low', label: 'low — 快速' },
  { value: 'medium', label: 'medium — 平衡' },
  { value: 'high', label: 'high — 深入' },
  { value: 'max', label: 'max — 最深(仅部分模型)' },
]

const MODE_OPTIONS: Array<{ value: TemperatureMode; label: string; hint: string }> = [
  { value: 'free', label: '自由', hint: '不限制调用方温度(默认)' },
  { value: 'exact', label: '固定值', hint: '强制所有请求使用固定温度(如 Kimi 只接受 1)' },
  { value: 'clamp', label: '区间裁剪', hint: '把请求温度裁剪到 [最小, 最大] 区间内' },
]

const GenerationTab: React.FC<Props> = ({ purpose, form, onChange }) => {
  return (
    <div className="space-y-5">
      {/* 思考策略 */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">思考策略</label>
        <select value={form.thinking_policy}
          onChange={(e) => onChange({ ...form, thinking_policy: e.target.value as ThinkingPolicy })}
          className="w-full border rounded-lg px-3 py-2 bg-white focus:outline-hidden focus:ring-2 focus:ring-primary">
          {THINKING_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="text-xs text-gray-400 mt-1">
          {THINKING_OPTIONS.find((o) => o.value === form.thinking_policy)?.hint}
        </p>
      </div>

      {/* 推理强度 */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">默认推理强度</label>
        <select value={form.reasoning_effort}
          onChange={(e) => onChange({ ...form, reasoning_effort: e.target.value as ReasoningEffort })}
          className="w-full border rounded-lg px-3 py-2 bg-white focus:outline-hidden focus:ring-2 focus:ring-primary">
          {EFFORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="text-xs text-gray-400 mt-1">
          {purpose === 'extract'
            ? '提取池同时服务机械节点与复审；建议关闭思考。仅支持 reasoning_effort 的厂商生效(DeepSeek/腾讯/AIHubMix)'
            : '仅支持 reasoning_effort 的厂商生效(DeepSeek/腾讯/AIHubMix)；其余自动忽略'}
        </p>
      </div>

      <div className="border-t border-gray-100 pt-4">
        {/* 采样温度 */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">默认采样温度</label>
          <input type="number" step="0.1" min="0" max="2" value={form.temperature}
            onChange={(e) => onChange({ ...form, temperature: e.target.value })}
            placeholder="留空使用默认值 0.1"
            className="w-full border rounded-lg px-3 py-2 focus:outline-hidden focus:ring-2 focus:ring-primary" />
          <p className="text-xs text-gray-400 mt-1">调用方未显式指定温度时使用；业务节点意图仍以 LLM Center 为准</p>
        </div>

        {/* 温度约束模式 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">温度能力约束</label>
          <div className="flex gap-2 mb-2">
            {MODE_OPTIONS.map((o) => (
              <button key={o.value} type="button"
                onClick={() => onChange({ ...form, temperature_mode: o.value })}
                className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
                  form.temperature_mode === o.value
                    ? 'border-primary bg-blue-50 text-primary font-medium'
                    : 'border-gray-200 text-gray-600 hover:border-gray-300'
                }`}>
                {o.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-400 mb-2">
            {MODE_OPTIONS.find((o) => o.value === form.temperature_mode)?.hint}
          </p>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={`block text-xs mb-1 ${form.temperature_mode === 'exact' ? 'text-gray-600' : 'text-gray-300'}`}>
                固定温度值
              </label>
              <input type="number" step="0.1" min="0" max="2"
                value={form.temperature_exact}
                disabled={form.temperature_mode !== 'exact'}
                onChange={(e) => onChange({ ...form, temperature_exact: e.target.value })}
                placeholder="如 1"
                className="w-full border rounded-lg px-3 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-300 focus:outline-hidden focus:ring-1 focus:ring-primary" />
            </div>
            <div>
              <label className={`block text-xs mb-1 ${form.temperature_mode === 'clamp' ? 'text-gray-600' : 'text-gray-300'}`}>
                最小温度
              </label>
              <input type="number" step="0.1" min="0" max="2"
                value={form.temperature_min}
                disabled={form.temperature_mode !== 'clamp'}
                onChange={(e) => onChange({ ...form, temperature_min: e.target.value })}
                placeholder="如 0"
                className="w-full border rounded-lg px-3 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-300 focus:outline-hidden focus:ring-1 focus:ring-primary" />
            </div>
            <div>
              <label className={`block text-xs mb-1 ${form.temperature_mode === 'clamp' ? 'text-gray-600' : 'text-gray-300'}`}>
                最大温度
              </label>
              <input type="number" step="0.1" min="0" max="2"
                value={form.temperature_max}
                disabled={form.temperature_mode !== 'clamp'}
                onChange={(e) => onChange({ ...form, temperature_max: e.target.value })}
                placeholder="如 1"
                className="w-full border rounded-lg px-3 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-300 focus:outline-hidden focus:ring-1 focus:ring-primary" />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default GenerationTab
