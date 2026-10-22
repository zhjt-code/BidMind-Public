import { create } from 'zustand'
import {
  writingService,
  type OutlineNode,
  type WritingMaterial,
  type WritingBinding,
  type SectionDraft,
  type ConsistencyIssue,
  type MaterialChecklistItem,
} from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'

interface WritingState {
  clauses: WritingClause[]
  outlineNodes: OutlineNode[]
  materials: WritingMaterial[]
  bindings: WritingBinding[]
  drafts: SectionDraft[]
  checklist: MaterialChecklistItem[]
  consistencyIssues: ConsistencyIssue[]
  currentNodeId: number | null
  loading: boolean
  error: string | null

  loadClauses: (projectId: number, layer?: string) => Promise<void>
  createClause: (projectId: number, data: Record<string, unknown>) => Promise<void>
  confirmClauses: (projectId: number, clauseIds: number[]) => Promise<void>
  deleteClause: (projectId: number, clauseId: number) => Promise<void>
  loadOutline: (projectId: number) => Promise<void>
  triggerOutlineGenerate: (projectId: number, force?: boolean) => Promise<void>
  createNode: (projectId: number, data: Record<string, unknown>) => Promise<void>
  updateNode: (projectId: number, nodeId: number, data: Record<string, unknown>) => Promise<void>
  deleteNode: (projectId: number, nodeId: number) => Promise<void>
  loadMaterials: (projectId: number) => Promise<void>
  loadChecklist: (projectId: number) => Promise<void>
  triggerMatch: (projectId: number, options?: { resetReviewedBindings?: boolean }) => Promise<void>
  loadBindings: (projectId: number) => Promise<void>
  loadDrafts: (projectId: number) => Promise<void>
  loadConsistency: (projectId: number) => Promise<void>
  setCurrentNodeId: (nodeId: number | null) => void
  reset: () => void
}

const initialState = {
  clauses: [] as WritingClause[],
  outlineNodes: [] as OutlineNode[],
  materials: [] as WritingMaterial[],
  bindings: [] as WritingBinding[],
  drafts: [] as SectionDraft[],
  checklist: [] as MaterialChecklistItem[],
  consistencyIssues: [] as ConsistencyIssue[],
  currentNodeId: null as number | null,
  loading: false,
  error: null as string | null,
}

export const useWritingStore = create<WritingState>((set) => ({
  ...initialState,

  loadClauses: async (projectId, layer) => {
    set({ loading: true, error: null })
    try {
      const clauses = await writingService.getClauses(projectId, layer)
      set({ clauses, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  createClause: async (projectId, data) => {
    try {
      await writingService.createClause(projectId, data)
      const clauses = await writingService.getClauses(projectId)
      set({ clauses })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  confirmClauses: async (projectId, clauseIds) => {
    try {
      await writingService.confirmClauses(projectId, clauseIds)
      set((state) => ({
        clauses: state.clauses.map((c) =>
          clauseIds.includes(c.id) ? { ...c, confirmed: 1 } : c
        ),
      }))
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  deleteClause: async (projectId, clauseId) => {
    try {
      await writingService.deleteClause(projectId, clauseId)
      const clauses = await writingService.getClauses(projectId)
      set({ clauses })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  loadOutline: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const outlineNodes = await writingService.getOutline(projectId)
      set({ outlineNodes, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  triggerOutlineGenerate: async (projectId, force) => {
    set({ loading: true, error: null })
    try {
      await writingService.generateOutline(projectId, force)
      set({ loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  createNode: async (projectId, data) => {
    try {
      await writingService.createNode(projectId, data)
      const outlineNodes = await writingService.getOutline(projectId)
      set({ outlineNodes })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  updateNode: async (projectId, nodeId, data) => {
    try {
      const updated = await writingService.updateNode(projectId, nodeId, data)
      set((state) => ({
        outlineNodes: state.outlineNodes.map((n) =>
          n.id === nodeId ? updated : n
        ),
      }))
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  deleteNode: async (projectId, nodeId) => {
    try {
      await writingService.deleteNode(projectId, nodeId)
      set((state) => {
        const idsToRemove = new Set<number>()
        const collectDescendants = (pid: number) => {
          idsToRemove.add(pid)
          for (const n of state.outlineNodes) {
            if (n.parentId === pid && !idsToRemove.has(n.id)) {
              collectDescendants(n.id)
            }
          }
        }
        collectDescendants(nodeId)
        return {
          outlineNodes: state.outlineNodes.filter((n) => !idsToRemove.has(n.id)),
        }
      })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  loadMaterials: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const materials = await writingService.getMaterials(projectId)
      set({ materials, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  loadChecklist: async (projectId) => {
    try {
      const checklist = await writingService.getChecklist(projectId)
      set({ checklist })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  triggerMatch: async (projectId, options) => {
    set({ loading: true, error: null })
    try {
      await writingService.triggerMatch(projectId, options)
      set({ loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  loadBindings: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const bindings = await writingService.getBindings(projectId)
      set({ bindings, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  loadDrafts: async (projectId) => {
    set({ loading: true, error: null })
    try {
      const drafts = await writingService.getDrafts(projectId)
      set({ drafts, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
      throw e
    }
  },

  loadConsistency: async (projectId) => {
    try {
      const consistencyIssues = await writingService.getConsistency(projectId)
      set({ consistencyIssues })
    } catch (e) {
      set({ error: String(e) })
      throw e
    }
  },

  setCurrentNodeId: (nodeId) => set({ currentNodeId: nodeId }),

  reset: () => set(initialState),
}))
