import React, { useState, useEffect, useCallback } from 'react'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import {
  llmCenterService,
  type LlmNodeDetail,
} from '@/services/llmCenterApi'
import { CHANNEL_COLORS, STATUS_COLORS } from '../lib/constants'
import { ParamTable } from './ParamTable'
import { PromptPreview } from './PromptPreview'
import { TracesTab } from './TracesTab'
import { HistoryTab } from './HistoryTab'

interface NodeDrawerProps {
  nodeKey: string | null
  onClose: () => void
  onChanged?: () => void
}

export const NodeDrawer: React.FC<NodeDrawerProps> = ({ nodeKey, onClose, onChanged }) => {
  const [detail, setDetail] = useState<LlmNodeDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'prompt' | 'params' | 'versions' | 'traces' | 'history'>('prompt')
  const [editing, setEditing] = useState<{ role: string; content: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const reload = useCallback(() => {
    if (!nodeKey) return
    setLoading(true)
    llmCenterService.getNodeDetail(nodeKey)
      .then(setDetail)
      .catch(() => toast.error('加载节点详情失败'))
      .finally(() => setLoading(false))
  }, [nodeKey])

  useEffect(() => {
    if (!nodeKey) { setDetail(null); setEditing(null); return }
    reload()
  }, [nodeKey, reload])

  const reloadAndNotify = () => {
    reload()
    onChanged?.()
  }

  const handleDiscardDraft = async () => {
    if (!nodeKey) return
    setSaving(true)
    try {
      const r = await llmCenterService.discardDraft(nodeKey)
      toast.success(`已丢弃 ${r.discarded} 个草稿版本`)
      reloadAndNotify()
    } catch { toast.error('丢弃草稿失败') }
    finally { setSaving(false) }
  }

  const handleSaveDraft = async () => {
    if (!nodeKey || !editing) return
    setSaving(true)
    try {
      await llmCenterService.createDraft(nodeKey, { template_role: editing.role, content: editing.content })
      toast.success('草稿已保存')
      setEditing(null)
      reloadAndNotify()
    } catch { toast.error('保存草稿失败') }
    finally { setSaving(false) }
  }

  const handleValidate = async () => {
    if (!nodeKey) return
    setSaving(true)
    try {
      const result = await llmCenterService.validateDraft(nodeKey)
      if (result.passed) toast.success('校验通过')
      else toast.error(`校验未通过: ${result.checks.filter(c => !c.passed).map(c => c.message).join('; ')}`)
      reloadAndNotify()
    } catch { toast.error('校验失败') }
    finally { setSaving(false) }
  }

  const handlePublish = async () => {
    if (!nodeKey) return
    setSaving(true)
    try {
      await llmCenterService.publishVersion(nodeKey)
      toast.success('发布成功')
      reloadAndNotify()
    } catch (e: unknown) { toast.error(`发布失败: ${e instanceof Error ? e.message : '未知错误'}`) }
    finally { setSaving(false) }
  }

  const handleRollback = async () => {
    if (!nodeKey) return
    setSaving(true)
    try {
      await llmCenterService.rollbackVersion(nodeKey)
      toast.success('回滚成功')
      reloadAndNotify()
    } catch { toast.error('回滚失败') }
    finally { setSaving(false) }
  }

  if (!nodeKey) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-auto animate-in slide-in-from-right">
        <div className="sticky top-0 bg-white border-b z-10 px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">{detail?.name || nodeKey}</h2>
              <p className="text-xs text-gray-500 font-mono mt-0.5">{nodeKey}</p>
            </div>
            <button onClick={onClose} className="p-1 rounded hover:bg-gray-100">
              <X className="h-5 w-5 text-gray-400" />
            </button>
          </div>

          {detail && (
            <div className="flex flex-wrap gap-2 mt-3 text-xs">
              <span className={`px-2 py-0.5 rounded-full ${CHANNEL_COLORS[detail.execution_channel]}`}>
                {detail.execution_channel}
              </span>
              <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{detail.call_method}</span>
              <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">T={detail.default_temperature}</span>
              <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">max={detail.default_max_tokens}</span>
              {detail.source_file && (
                <span className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 font-mono">{detail.source_file}</span>
              )}
            </div>
          )}

          <div className="flex gap-1 mt-4 border-b -mb-px overflow-x-auto">
            {([
              ['prompt', 'Prompt'], ['params', '参数'], ['versions', '版本'],
              ['traces', '调用轨迹'], ['history', '发布历史'],
            ] as const).map(([tab, label]) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab
                    ? 'border-primary text-primary'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="px-6 py-4">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}

          {!loading && detail && activeTab === 'prompt' && (
            <div>
              {editing ? (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-gray-600 uppercase">编辑 {editing.role}</span>
                    <div className="flex gap-2">
                      <button onClick={() => setEditing(null)} className="px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 rounded">取消</button>
                      <button onClick={handleSaveDraft} disabled={saving} className="px-3 py-1 text-xs bg-primary text-white rounded hover:bg-primary/90 disabled:opacity-50">
                        {saving ? '保存中...' : '保存草稿'}
                      </button>
                    </div>
                  </div>
                  <textarea
                    value={editing.content}
                    onChange={e => setEditing({ ...editing, content: e.target.value })}
                    className="w-full h-64 font-mono text-xs p-3 bg-gray-900 text-gray-100 rounded-lg resize-y border-0 focus:ring-2 focus:ring-primary"
                  />
                </div>
              ) : (
                <>
                  {detail.all_versions.filter(v => v.is_active).length === 0 ? (
                    <p className="text-sm text-gray-400 italic">无激活 Prompt 版本</p>
                  ) : (
                    detail.all_versions
                      .filter(v => v.is_active)
                      .sort((a, _b) => (a.template_role === 'system' ? -1 : 1))
                      .map(v => (
                        <div key={v.id}>
                          <PromptPreview version={v} />
                          {detail.editable_level !== 'readonly' && (
                            <button
                              onClick={() => setEditing({ role: v.template_role, content: v.content })}
                              className="text-xs text-primary hover:underline mb-3 inline-block"
                            >
                              编辑此模板
                            </button>
                          )}
                          {detail.editable_level === 'readonly' && (
                            <span className="text-xs text-gray-400 mb-3 inline-block">只读节点</span>
                          )}
                        </div>
                      ))
                  )}
                  {(() => {
                    const drafts = detail.all_versions.filter(v => v.status === 'draft')
                    const validated = detail.all_versions.filter(v => v.status === 'validated')
                    const hasDrafts = drafts.length > 0
                    const hasValidated = validated.length > 0
                    if (!hasDrafts && !hasValidated) return null
                    const validatedRoles = validated.map(v => v.template_role.toUpperCase()).join(', ')
                    const draftRoles = drafts.map(v => v.template_role.toUpperCase()).join(', ')
                    return (
                      <>
                        {hasDrafts && (
                          <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                            <p className="text-xs text-yellow-700 mb-2">
                              [{draftRoles}] 有未校验的草稿
                            </p>
                            <div className="flex gap-2">
                              <button onClick={handleDiscardDraft} disabled={saving} className="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-100 disabled:opacity-50">丢弃草稿</button>
                              <button onClick={handleValidate} disabled={saving} className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">校验</button>
                            </div>
                          </div>
                        )}
                        {hasValidated && (
                          <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg">
                            <p className="text-xs text-green-700 mb-2">
                              [{validatedRoles}] 已通过校验，可以发布
                            </p>
                            <div className="flex gap-2">
                              <button onClick={handleDiscardDraft} disabled={saving} className="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-100 disabled:opacity-50">丢弃草稿</button>
                              <button onClick={handlePublish} disabled={saving} className="px-3 py-1.5 text-xs bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50">发布</button>
                            </div>
                          </div>
                        )}
                      </>
                    )
                  })()}
                  {detail.all_versions.filter(v => v.status === 'archived').length > 0 && (
                    <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded-lg">
                      <p className="text-xs text-gray-500 mb-2">可回滚到上一个已发布版本</p>
                      <button onClick={handleRollback} disabled={saving} className="px-3 py-1.5 text-xs border border-orange-300 text-orange-600 rounded hover:bg-orange-50 disabled:opacity-50">恢复上一版本</button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {!loading && detail && activeTab === 'params' && (
            <div className="space-y-4">
              <div>
                <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Input</h4>
                <ParamTable params={detail.params} direction="input" />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Output</h4>
                <ParamTable params={detail.params} direction="output" />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Runtime</h4>
                <ParamTable params={detail.params} direction="runtime" />
              </div>
            </div>
          )}

          {!loading && detail && activeTab === 'versions' && (
            <div className="space-y-2">
              {detail.all_versions.length === 0 ? (
                <p className="text-sm text-gray-400 italic">无版本记录</p>
              ) : (
                detail.all_versions.map(v => (
                  <div key={v.id} className={`p-3 rounded-lg border ${v.is_active ? 'border-green-300 bg-green-50/30' : 'border-gray-200'}`}>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-medium text-gray-700">v{v.version}</span>
                      <span className="uppercase text-gray-500">{v.template_role}</span>
                      <span className={`px-1.5 py-0.5 rounded-full ${STATUS_COLORS[v.status]}`}>{v.status}</span>
                      <span className="text-gray-400">{v.source}</span>
                      {v.is_active && <span className="text-green-600 font-semibold">ACTIVE</span>}
                      <span className="ml-auto text-gray-400">{new Date(v.created_at).toLocaleDateString()}</span>
                    </div>
                    {v.change_note && <p className="text-xs text-gray-500 mt-1">{v.change_note}</p>}
                  </div>
                ))
              )}
            </div>
          )}

          {!loading && nodeKey && activeTab === 'traces' && (
            <TracesTab nodeKey={nodeKey} />
          )}

          {!loading && nodeKey && activeTab === 'history' && (
            <HistoryTab nodeKey={nodeKey} />
          )}
        </div>
      </div>
    </div>
  )
}
