/**
 * 素材标题内联编辑 hook。
 *
 * 抽自 WritingMaterials.tsx 的 editingMaterialId / editingTitleValue /
 * pendingTitleEdits / savingPendingTitles 状态，以及
 * handleStartEditTitle / handleCancelTitleEdit / handleCommitTitleEdit /
 * getDisplayedTitle 四个函数。
 *
 * 教训提示：本 hook 暴露 resetAll() 集中清理三种状态，防止外部使用时遗漏字段。
 */

import { useCallback, useMemo, useState } from 'react'
import type { WritingMaterial } from '@/services/writingApi'
import { getMaterialDisplayName } from '../../materialDisplay.ts'

interface UseMaterialTitleEditingOptions {
  materials: WritingMaterial[]
}

export interface MaterialTitleEditingController {
  editingMaterialId: number | null
  editingTitleValue: string
  pendingTitleEdits: Record<number, string>
  pendingTitleCount: number
  savingPendingTitles: boolean
  setEditingTitleValue: (value: string) => void
  setSavingPendingTitles: (saving: boolean) => void
  clearPendingTitles: () => void
  /** 一次性清理三种状态（用于切项目） */
  resetAll: () => void
  startEdit: (material: WritingMaterial) => void
  cancelEdit: () => void
/**
 * 提交内联编辑：
 * - 返回 true：提交完成（包括内容为空白回退取消的情况，调用方可继续 next 流程）
 * - 返回 false：因输入空白而被拒绝（不应继续 next 流程）
 */
  commitEdit: () => boolean
  getDisplayedTitle: (material: WritingMaterial) => string
}

export function useMaterialTitleEditing(
  options: UseMaterialTitleEditingOptions,
): MaterialTitleEditingController {
  const { materials } = options
  const [editingMaterialId, setEditingMaterialId] = useState<number | null>(null)
  const [editingTitleValue, setEditingTitleValue] = useState('')
  const [pendingTitleEdits, setPendingTitleEdits] = useState<Record<number, string>>({})
  const [savingPendingTitles, setSavingPendingTitles] = useState(false)

  const pendingTitleCount = useMemo(
    () => Object.keys(pendingTitleEdits).length,
    [pendingTitleEdits],
  )

  const cancelEdit = useCallback(() => {
    setEditingMaterialId(null)
    setEditingTitleValue('')
  }, [])

  const startEdit = useCallback(
    (material: WritingMaterial) => {
      setEditingMaterialId(material.id)
      setEditingTitleValue(pendingTitleEdits[material.id] ?? getMaterialDisplayName(material))
    },
    [pendingTitleEdits],
  )

  const commitEdit = useCallback((): boolean => {
    if (editingMaterialId == null) return true
    const normalized = editingTitleValue.trim()
    const material = materials.find((item) => item.id === editingMaterialId)
    if (!material) {
      cancelEdit()
      return true
    }
    if (!normalized) {
      cancelEdit()
      return false
    }
    const persistedTitle = getMaterialDisplayName(material)
    setPendingTitleEdits((prev) => {
      const next = { ...prev }
      if (normalized === persistedTitle) {
        delete next[editingMaterialId]
      } else {
        next[editingMaterialId] = normalized
      }
      return next
    })
    cancelEdit()
    return true
  }, [editingMaterialId, editingTitleValue, materials, cancelEdit])

  const getDisplayedTitle = useCallback(
    (material: WritingMaterial): string => {
      return pendingTitleEdits[material.id] ?? getMaterialDisplayName(material)
    },
    [pendingTitleEdits],
  )

  const clearPendingTitles = useCallback(() => {
    setPendingTitleEdits({})
  }, [])

  const resetAll = useCallback(() => {
    setEditingMaterialId(null)
    setEditingTitleValue('')
    setPendingTitleEdits({})
  }, [])

  return {
    editingMaterialId,
    editingTitleValue,
    pendingTitleEdits,
    pendingTitleCount,
    savingPendingTitles,
    setEditingTitleValue,
    setSavingPendingTitles,
    clearPendingTitles,
    resetAll,
    startEdit,
    cancelEdit,
    commitEdit,
    getDisplayedTitle,
  }
}
