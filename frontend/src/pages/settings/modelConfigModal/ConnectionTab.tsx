/**
 * P0-C 模型配置 Modal — 连接 Tab
 *
 * 职责: API 地址、模型名、主密钥与调度密钥编辑(原内联 ConfigModal 字段迁移)
 * 不做: 不承担能力/生成参数(归 CapabilityTab/GenerationTab)、不发请求
 */

import React from 'react'
import { MASKED_KEY, nextApiKeyUid, type ModelConfigForm } from './types'

interface Props {
  form: ModelConfigForm
  onChange: (form: ModelConfigForm) => void
}

const ConnectionTab: React.FC<Props> = ({ form, onChange }) => {
  const isCloud = !form.api_base.includes('localhost')
    && !form.api_base.includes('127.0.0.1')
    && form.api_base.length > 0

  const addApiKey = () => {
    onChange({
      ...form,
      api_keys: [...form.api_keys, { _uid: nextApiKeyUid(), key: '', label: '', enabled: true }],
    })
  }
  const removeApiKey = (idx: number) => {
    onChange({ ...form, api_keys: form.api_keys.filter((_, i) => i !== idx) })
  }
  const updateApiKey = (idx: number, field: string, value: string | boolean) => {
    onChange({
      ...form,
      api_keys: form.api_keys.map((k, i) => (i === idx ? { ...k, [field]: value } : k)),
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">API基础URL</label>
        <input type="text" value={form.api_base}
          onChange={(e) => onChange({ ...form, api_base: e.target.value })}
          placeholder="http://localhost:11434/v1 或 https://api.deepseek.com"
          className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary" />
        <p className="text-xs text-gray-400 mt-1">本地: http://localhost:11434/v1 | 云端: https://api.deepseek.com</p>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
        <input type="text" value={form.model_name}
          onChange={(e) => onChange({ ...form, model_name: e.target.value })}
          placeholder="qwen3-vl:4b 或 deepseek-chat"
          className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary" />
        <p className="text-xs text-gray-400 mt-1">须与厂商模型 ID 完全一致，保存后建议执行「保存并验证」</p>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">API密钥</label>
        <input type="password" value={form.api_key}
          onChange={(e) => onChange({ ...form, api_key: e.target.value })}
          onFocus={(e) => {
            if (form._hasExistingKey && e.target.value === MASKED_KEY) {
              onChange({ ...form, api_key: '', _hasExistingKey: false })
            }
          }}
          placeholder="请配置密钥"
          className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary" />
      </div>

      {isCloud && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="block text-sm font-medium text-gray-700">调度密钥</label>
            <button type="button" onClick={addApiKey}
              className="text-xs text-primary hover:text-blue-600 flex items-center">
              <svg className="w-3 h-3 mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              添加密钥
            </button>
          </div>
          <div className="space-y-2 max-h-[30vh] overflow-y-auto overscroll-contain pr-1">
            {form.api_keys.map((ki, idx) => (
              <div key={ki._uid} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2 bg-gray-50 rounded-lg">
                <input type="password" value={ki.key}
                  onChange={(e) => updateApiKey(idx, 'key', e.target.value)}
                  onFocus={(e) => {
                    if (ki._isExisting && e.target.value === MASKED_KEY) {
                      onChange({
                        ...form,
                        api_keys: form.api_keys.map((k, i) =>
                          i === idx ? { ...k, key: '', _isExisting: false } : k),
                      })
                    }
                  }}
                  placeholder="请配置密钥"
                  className="w-full sm:flex-1 sm:min-w-0 border rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
                <input type="text" value={ki.label}
                  onChange={(e) => updateApiKey(idx, 'label', e.target.value)}
                  placeholder="标签"
                  className="w-full sm:w-20 border rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
                <label className="flex items-center text-xs text-gray-500 whitespace-nowrap">
                  <input type="checkbox" checked={ki.enabled}
                    onChange={(e) => updateApiKey(idx, 'enabled', e.target.checked)}
                    className="mr-1 rounded" />
                  启用
                </label>
                <button type="button" onClick={() => removeApiKey(idx)}
                  className="self-start sm:self-auto text-red-400 hover:text-red-600 p-1">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default ConnectionTab
