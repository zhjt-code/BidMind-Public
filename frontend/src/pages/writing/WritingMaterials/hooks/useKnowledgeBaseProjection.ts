/**
 * 知识库素材投影 hook。
 *
 * 抽自 WritingMaterials.tsx 的 kbDialogOpen / kbDocs / kbLoading / kbProjecting /
 * selectedKbIds / kbAtomMap / selectedKbAtomIds 状态，以及 loadKbDocs /
 * openKbDialog / toggleKbSelection / toggleKbAtomSelection / handleProjectKbMaterials
 * 五个动作。
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { knowledgeBaseService } from '@/services/knowledgeBaseApi'
import type { KnowledgeAtom, KnowledgeDocument } from '@/services/knowledgeBaseApi'
import { canAutoUseKnowledgeDocument } from '@/services/knowledgeBaseView'

interface UseKnowledgeBaseProjectionOptions {
  pid: number
  /** 投影完成后同步素材列表 */
  onMaterialsChanged: () => Promise<void>
}

export interface KnowledgeBaseProjectionController {
  kbDialogOpen: boolean
  kbLoading: boolean
  kbProjecting: boolean
  kbDocs: KnowledgeDocument[]
  kbAtomMap: Record<number, KnowledgeAtom[]>
  selectedKbIds: number[]
  selectedKbAtomIds: number[]
  setKbDialogOpen: (open: boolean) => void
  openKbDialog: () => Promise<void>
  toggleKbSelection: (docId: number) => void
  toggleKbAtomSelection: (atomId: number) => void
  handleProjectKbMaterials: () => Promise<void>
}

export function useKnowledgeBaseProjection(
  options: UseKnowledgeBaseProjectionOptions,
): KnowledgeBaseProjectionController {
  const { pid, onMaterialsChanged } = options

  const [kbDialogOpen, setKbDialogOpen] = useState(false)
  const [kbDocs, setKbDocs] = useState<KnowledgeDocument[]>([])
  const [kbLoading, setKbLoading] = useState(false)
  const [kbProjecting, setKbProjecting] = useState(false)
  const [selectedKbIds, setSelectedKbIds] = useState<number[]>([])
  const [kbAtomMap, setKbAtomMap] = useState<Record<number, KnowledgeAtom[]>>({})
  const [selectedKbAtomIds, setSelectedKbAtomIds] = useState<number[]>([])

  const loadKbDocs = useCallback(async () => {
    setKbLoading(true)
    try {
      const docs = await knowledgeBaseService.listDocuments({
        applicableFlow: 'bid_writing',
        includeDisabled: false,
      })
      const usableDocs = docs.filter((doc) => canAutoUseKnowledgeDocument(doc))
      setKbDocs(usableDocs)
      const atomEntries = await Promise.all(
        usableDocs.map(async (doc) => [doc.id, await knowledgeBaseService.listAtoms(doc.id)] as const),
      )
      setKbAtomMap(Object.fromEntries(atomEntries))
    } catch {
      toast.error('知识库素材加载失败')
    } finally {
      setKbLoading(false)
    }
  }, [])

  const openKbDialog = useCallback(async () => {
    setKbDialogOpen(true)
    await loadKbDocs()
  }, [loadKbDocs])

  const toggleKbSelection = useCallback((docId: number) => {
    setSelectedKbIds((prev) =>
      prev.includes(docId) ? prev.filter((id) => id !== docId) : [...prev, docId],
    )
  }, [])

  const toggleKbAtomSelection = useCallback((atomId: number) => {
    setSelectedKbAtomIds((prev) =>
      prev.includes(atomId) ? prev.filter((id) => id !== atomId) : [...prev, atomId],
    )
  }, [])

  const handleProjectKbMaterials = useCallback(async () => {
    if (selectedKbIds.length === 0 && selectedKbAtomIds.length === 0) {
      toast.warning('请先选择知识库资料或原子素材')
      return
    }
    setKbProjecting(true)
    try {
      const result = selectedKbAtomIds.length > 0
        ? await knowledgeBaseService.projectAtomsToWriting(pid, selectedKbAtomIds)
        : await knowledgeBaseService.projectToWriting(pid, selectedKbIds)
      await onMaterialsChanged()
      setSelectedKbIds([])
      setSelectedKbAtomIds([])
      setKbDialogOpen(false)
      const warningText = result.warnings.length > 0 ? `，${result.warnings[0]}` : ''
      toast.success(`已引用 ${result.createdMaterialIds.length} 份知识库素材${warningText}`)
    } catch {
      toast.error('引用知识库素材失败')
    } finally {
      setKbProjecting(false)
    }
  }, [pid, selectedKbIds, selectedKbAtomIds, onMaterialsChanged])

  return {
    kbDialogOpen,
    kbLoading,
    kbProjecting,
    kbDocs,
    kbAtomMap,
    selectedKbIds,
    selectedKbAtomIds,
    setKbDialogOpen,
    openKbDialog,
    toggleKbSelection,
    toggleKbAtomSelection,
    handleProjectKbMaterials,
  }
}
