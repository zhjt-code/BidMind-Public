/**
 * 职责: 项目级知识库纳入配置的加载、脏检查与保存
 * 不做: 不渲染 UI、不管理资料启用/停用、不发起审查
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  knowledgeBaseService,
  type KnowledgeDocument,
  type KnowledgeUsageConfig,
} from '@/services/knowledgeBaseApi'
import { canAutoUseKnowledgeDocument } from '@/services/knowledgeBaseView'
import {
  KNOWLEDGE_USAGE_LOAD_FAILED,
  KNOWLEDGE_USAGE_SAVE_FAILED,
  KNOWLEDGE_USAGE_SAVED,
  canSaveKnowledgeUsage,
  enabledFromKnowledgeUsageMode,
  isKnowledgeUsageDirty,
  type KnowledgeUsageFlow,
  type KnowledgeUsageMode,
} from '@/components/knowledgeUsagePanelView'

export function useProjectKnowledgeUsageEditor(
  projectId: number,
  flow: KnowledgeUsageFlow,
  onChange?: (config: KnowledgeUsageConfig) => void,
) {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([])
  const [config, setConfig] = useState<KnowledgeUsageConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [pickerOpen, setPickerOpen] = useState(false)

  const loadAll = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const [usableDocs, projectConfig] = await Promise.all([
        knowledgeBaseService.listDocuments({ applicableFlow: flow, includeDisabled: false }),
        knowledgeBaseService.getProjectUsageConfig(projectId),
      ])
      setDocs(usableDocs.filter((doc) => canAutoUseKnowledgeDocument(doc)))
      setConfig(projectConfig)
      setEnabled(projectConfig.enabled && projectConfig.flow === flow)
      setSelectedIds(projectConfig.flow === flow ? projectConfig.selectedKbDocumentIds : [])
    } catch {
      toast.error(KNOWLEDGE_USAGE_LOAD_FAILED)
    } finally {
      setLoading(false)
    }
  }, [projectId, flow])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const savedIds = config?.flow === flow ? config.selectedKbDocumentIds : []
  const isDirty = useMemo(
    () =>
      isKnowledgeUsageDirty({
        configEnabled: Boolean(config?.enabled),
        configFlow: config?.flow,
        currentFlow: flow,
        enabled,
        selectedIds,
        savedIds,
      }),
    [config, flow, enabled, selectedIds, savedIds],
  )
  const canSave = canSaveKnowledgeUsage(isDirty, enabled, selectedIds)

  const persistKnowledgeUsage = useCallback(
    async (nextEnabled: boolean, nextIds: number[]) => {
      setSaving(true)
      try {
        const updated = await knowledgeBaseService.updateProjectUsageConfig(projectId, {
          enabled: nextEnabled,
          flow,
          selectedKbDocumentIds: nextEnabled ? nextIds : [],
        })
        setConfig(updated)
        setEnabled(updated.enabled && updated.flow === flow)
        setSelectedIds(updated.flow === flow ? updated.selectedKbDocumentIds : [])
        toast.success(KNOWLEDGE_USAGE_SAVED)
        onChange?.(updated)
        return true
      } catch {
        toast.error(KNOWLEDGE_USAGE_SAVE_FAILED)
        return false
      } finally {
        setSaving(false)
      }
    },
    [projectId, flow, onChange],
  )

  const applyKnowledgeUsageMode = useCallback(
    async (mode: KnowledgeUsageMode) => {
      const nextEnabled = enabledFromKnowledgeUsageMode(mode)
      if (!nextEnabled) {
        const savedEnabled = Boolean(config?.enabled && config.flow === flow)
        if (savedEnabled) {
          const ok = await persistKnowledgeUsage(false, [])
          if (!ok) return
        } else {
          setEnabled(false)
        }
        setPickerOpen(false)
        return
      }
      setEnabled(true)
      setPickerOpen(true)
    },
    [config, flow, persistKnowledgeUsage],
  )

  const toggleKnowledgeDocument = useCallback((docId: number) => {
    setSelectedIds((prev) =>
      prev.includes(docId) ? prev.filter((id) => id !== docId) : [...prev, docId],
    )
  }, [])

  const saveKnowledgeUsage = useCallback(async () => {
    const ok = await persistKnowledgeUsage(true, selectedIds)
    if (ok) setPickerOpen(false)
  }, [persistKnowledgeUsage, selectedIds])

  return {
    docs,
    config,
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
  }
}
