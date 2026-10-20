import React from 'react'
import { Loader2 } from 'lucide-react'
import type { WritingMaterial } from '@/services/writingApi'
import type { ChildGroupKey } from '../types'
import { MaterialRow } from './MaterialRow.tsx'

interface MaterialListPanelProps {
  loading: boolean
  isProcessing: boolean
  materials: WritingMaterial[]
  rootMaterials: WritingMaterial[]
  childMap: Map<number, WritingMaterial[]>
  groupedChildMap: Map<number, Record<ChildGroupKey, WritingMaterial[]>>
  savingPendingTitles: boolean
  editingMaterialId: number | null
  editingTitleValue: string
  pendingTitleEdits: Record<number, string>
  getDisplayedTitle: (material: WritingMaterial) => string
  onChangeTitleValue: (value: string) => void
  onCommitTitleEdit: () => void
  onCancelTitleEdit: () => void
  onStartEditTitle: (material: WritingMaterial) => void
  onOpenPdfPreview: (material: WritingMaterial) => void
  onMaterialAction: (materialId: number, action: string, successMessage: string) => void
  onDelete: (materialId: number) => void
}

export const MaterialListPanel: React.FC<MaterialListPanelProps> = ({
  loading,
  isProcessing,
  materials,
  rootMaterials,
  childMap,
  groupedChildMap,
  savingPendingTitles,
  editingMaterialId,
  editingTitleValue,
  pendingTitleEdits,
  getDisplayedTitle,
  onChangeTitleValue,
  onCommitTitleEdit,
  onCancelTitleEdit,
  onStartEditTitle,
  onOpenPdfPreview,
  onMaterialAction,
  onDelete,
}) => {
  if (!(loading || isProcessing || materials.length > 0)) return null

  return (
    <div className="bg-white rounded-xl border mb-4">
      {loading ? (
        <div className="text-center py-12 text-muted-foreground">加载中...</div>
      ) : materials.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <Loader2 className="h-8 w-8 mx-auto mb-2 animate-spin opacity-50" />
          <p>素材正在处理中...</p>
        </div>
      ) : (
        <div className="divide-y">
          {rootMaterials.map((mat) => (
            <MaterialRow
              key={mat.id}
              material={mat}
              children={childMap.get(mat.id) || []}
              childGroups={groupedChildMap.get(mat.id)}
              isProcessing={isProcessing}
              savingPendingTitles={savingPendingTitles}
              editingMaterialId={editingMaterialId}
              editingTitleValue={editingTitleValue}
              pendingTitleEdits={pendingTitleEdits}
              getDisplayedTitle={getDisplayedTitle}
              onChangeTitleValue={onChangeTitleValue}
              onCommitTitleEdit={onCommitTitleEdit}
              onCancelTitleEdit={onCancelTitleEdit}
              onStartEditTitle={onStartEditTitle}
              onOpenPdfPreview={onOpenPdfPreview}
              onMaterialAction={onMaterialAction}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}
