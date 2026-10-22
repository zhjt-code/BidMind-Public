/**
 * 职责: 项目级知识库纳入仪器（关闭/参考）与资料选择弹出层
 * 不做: 不管理知识库资料生命周期、不发起审查、不写入写标素材
 */

import React, { useEffect, useMemo, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import FieldHintButton from '@/components/upload/FieldHintButton'
import SegmentedControl from '@/components/upload/SegmentedControl'
import { useProjectKnowledgeUsageEditor } from '@/components/knowledgeUsageEditorState'
import {
  KNOWLEDGE_USAGE_EMPTY,
  KNOWLEDGE_USAGE_HINT,
  KNOWLEDGE_USAGE_LABEL,
  KNOWLEDGE_USAGE_MODE_OPTIONS,
  KNOWLEDGE_USAGE_PICKER_TITLE,
  KNOWLEDGE_USAGE_SAVE,
  formatKnowledgeUsageReadOnlySummary,
  formatKnowledgeUsageSelectedCount,
  knowledgeUsageModeFromEnabled,
  shouldRenderReadOnlyKnowledgeUsagePanel,
  type KnowledgeUsageFlow,
  type KnowledgeUsageMode,
} from '@/components/knowledgeUsagePanelView'
import type { KnowledgeDocument, KnowledgeUsageConfig } from '@/services/knowledgeBaseApi'
import {
  getKnowledgeBusinessTypeLabel,
  getKnowledgeUnavailableReason,
} from '@/services/knowledgeBaseView'

interface KnowledgeUsagePanelProps {
  projectId: number
  flow: KnowledgeUsageFlow
  readOnly?: boolean
  title?: string
  description?: string
  onChange?: (config: KnowledgeUsageConfig) => void
}

interface KnowledgeUsagePickerModel {
  docs: KnowledgeDocument[]
  selectedIds: number[]
  loading: boolean
  saving: boolean
  canSave: boolean
  onToggleDoc: (docId: number) => void
  onSave: () => void
}

const KnowledgeUsagePicker: React.FC<{ model: KnowledgeUsagePickerModel }> = ({ model }) => (
  <div className="absolute right-0 top-full z-30 mt-1 w-[min(calc(100vw-2rem),22rem)] rounded-md border border-[rgba(28,39,51,0.14)] bg-white p-3 shadow-md">
    <div className="mb-2 flex items-center justify-between gap-2">
      <p className="text-sm font-medium text-[#1c2733]">{KNOWLEDGE_USAGE_PICKER_TITLE}</p>
      <p className="text-xs text-gray-500">{formatKnowledgeUsageSelectedCount(model.selectedIds.length)}</p>
    </div>
    {model.loading ? (
      <div className="flex items-center gap-1 text-xs text-gray-500">
        <Loader2 className="h-3 w-3 animate-spin" /> 加载资料…
      </div>
    ) : model.docs.length === 0 ? (
      <p className="text-xs text-gray-500">{KNOWLEDGE_USAGE_EMPTY}</p>
    ) : (
      <div className="mb-2 max-h-[220px] divide-y overflow-y-auto rounded-md border">
        {model.docs.map((doc) => {
          const checked = model.selectedIds.includes(doc.id)
          const reason = getKnowledgeUnavailableReason(doc)
          return (
            <button
              key={doc.id}
              type="button"
              onClick={() => model.onToggleDoc(doc.id)}
              className={`w-full px-3 py-2 text-left transition ${checked ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{doc.title}</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <Badge variant="outline">{getKnowledgeBusinessTypeLabel(doc.businessType)}</Badge>
                    <Badge variant="secondary">v{doc.version || '1'}</Badge>
                    {reason && <Badge className="bg-red-100 text-red-700">{reason}</Badge>}
                  </div>
                </div>
                <input type="checkbox" readOnly checked={checked} className="mt-1 shrink-0" />
              </div>
            </button>
          )
        })}
      </div>
    )}
    <div className="flex justify-end">
      <Button size="sm" onClick={model.onSave} disabled={model.saving || !model.canSave}>
        {model.saving && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
        {KNOWLEDGE_USAGE_SAVE}
      </Button>
    </div>
  </div>
)

const KnowledgeUsageReadOnlySummary: React.FC<{
  flow: KnowledgeUsageFlow
  projectId: number
}> = ({ flow, projectId }) => {
  const { docs, config, loading } = useProjectKnowledgeUsageEditor(projectId, flow)
  const activeDocIds = useMemo(() => {
    if (!config || !config.enabled || config.flow !== flow) return [] as number[]
    return config.selectedKbDocumentIds
  }, [config, flow])
  const shouldRender = shouldRenderReadOnlyKnowledgeUsagePanel({
    loading,
    enabled: Boolean(config?.enabled),
    configFlow: config?.flow,
    currentFlow: flow,
    selectedDocumentIds: activeDocIds,
  })
  if (!shouldRender) return null
  const titles = activeDocIds
    .map((docId) => docs.find((doc) => doc.id === docId)?.title)
    .filter((title): title is string => Boolean(title))
    .join('、')
  return (
    <p className="max-w-[16rem] truncate text-xs text-gray-500" title={titles || undefined}>
      {loading ? (
        <span className="inline-flex items-center gap-1">
          <Loader2 className="h-3 w-3 animate-spin" /> 知识库 · 加载中
        </span>
      ) : (
        formatKnowledgeUsageReadOnlySummary(activeDocIds.length)
      )}
    </p>
  )
}

const KnowledgeUsageEditor: React.FC<{
  projectId: number
  flow: KnowledgeUsageFlow
  hint: string
  onChange?: (config: KnowledgeUsageConfig) => void
}> = ({ projectId, flow, hint, onChange }) => {
  const rootRef = useRef<HTMLDivElement>(null)
  const {
    docs,
    loading,
    saving,
    enabled,
    selectedIds,
    pickerOpen,
    setPickerOpen,
    isDirty,
    canSave,
    applyKnowledgeUsageMode,
    toggleKnowledgeDocument,
    saveKnowledgeUsage,
  } = useProjectKnowledgeUsageEditor(projectId, flow, onChange)

  useEffect(() => {
    if (!pickerOpen) return
    const closePickerOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', closePickerOnOutside)
    return () => document.removeEventListener('mousedown', closePickerOnOutside)
  }, [pickerOpen, setPickerOpen])

  return (
    <div ref={rootRef} className="relative flex flex-col items-stretch gap-1 sm:items-end">
      <div className="flex items-center gap-2">
        <p className="shrink-0 text-sm font-medium text-[#1c2733]">{KNOWLEDGE_USAGE_LABEL}</p>
        <SegmentedControl
          ariaLabel={KNOWLEDGE_USAGE_LABEL}
          value={knowledgeUsageModeFromEnabled(enabled)}
          options={KNOWLEDGE_USAGE_MODE_OPTIONS}
          disabled={saving}
          onChange={(next) => {
            void applyKnowledgeUsageMode(next as KnowledgeUsageMode)
          }}
        />
        {enabled && !pickerOpen ? (
          <button
            type="button"
            className="shrink-0 text-xs text-gray-500 hover:text-[#1c2733]"
            onClick={() => setPickerOpen(true)}
          >
            {isDirty ? '未保存' : formatKnowledgeUsageSelectedCount(selectedIds.length)}
          </button>
        ) : null}
        <FieldHintButton label={hint}>{hint}</FieldHintButton>
      </div>
      {pickerOpen ? (
        <KnowledgeUsagePicker
          model={{
            docs,
            selectedIds,
            loading,
            saving,
            canSave,
            onToggleDoc: toggleKnowledgeDocument,
            onSave: () => {
              void saveKnowledgeUsage()
            },
          }}
        />
      ) : null}
    </div>
  )
}

const KnowledgeUsagePanel: React.FC<KnowledgeUsagePanelProps> = ({
  projectId,
  flow,
  readOnly = false,
  description,
  onChange,
}) => {
  const hint = description || KNOWLEDGE_USAGE_HINT[flow]
  if (readOnly) {
    return <KnowledgeUsageReadOnlySummary projectId={projectId} flow={flow} />
  }
  return (
    <KnowledgeUsageEditor
      projectId={projectId}
      flow={flow}
      hint={hint}
      onChange={onChange}
    />
  )
}

export default KnowledgeUsagePanel
