import { useEffect, useMemo, useState } from 'react'
import type { Project } from '@/stores/projectStore'
import { useInvalidateProjectDashboard } from '@/hooks/useProjectDashboard'

interface UseProjectListManagementArgs {
  projects: Project[]
  pagedProjects: Project[]
  userId?: number
  isAdmin: boolean
  deleteProject: (id: number) => Promise<void>
  batchDeleteProjects: (ids: number[]) => Promise<unknown>
  selectionResetKey: string
}

export function useProjectListManagement({
  projects,
  pagedProjects,
  userId,
  isAdmin,
  deleteProject,
  batchDeleteProjects,
  selectionResetKey,
}: UseProjectListManagementArgs) {
  const [deleteConfirm, setDeleteConfirm] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [batchDeleteConfirm, setBatchDeleteConfirm] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)
  // 批量删除开始前冻结目标快照，避免乐观更新后对话框数量归零。
  const [batchDeleteTargets, setBatchDeleteTargets] = useState<Project[]>([])
  const invalidateProjectDashboard = useInvalidateProjectDashboard()

  const selectableProjects = useMemo(
    () => pagedProjects.filter((project) => project.ownerId === userId || isAdmin),
    [pagedProjects, userId, isAdmin],
  )
  const selectedProjects = useMemo(
    () => projects.filter((project) => selectedIds.has(project.id)),
    [projects, selectedIds],
  )

  const exitSelectionMode = () => {
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  const toggleSelectionMode = () => {
    if (selectionMode) {
      exitSelectionMode()
      return
    }
    setSelectionMode(true)
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    const selectableIds = selectableProjects.map((project) => project.id)
    const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id))
    setSelectedIds(allSelected ? new Set() : new Set(selectableIds))
  }

  const openBatchDeleteConfirm = () => {
    setBatchDeleteTargets(selectedProjects)
    setBatchDeleteConfirm(true)
  }

  const handleDeleteProject = async () => {
    if (!deleteConfirm || deleting) return
    setDeleting(true)
    try {
      await deleteProject(deleteConfirm.id)
      await invalidateProjectDashboard()
    } finally {
      setDeleting(false)
      setDeleteConfirm(null)
    }
  }

  const handleBatchDelete = async () => {
    const ids = batchDeleteTargets.map((p) => p.id)
    if (ids.length === 0) return
    setBatchDeleting(true)
    try {
      await batchDeleteProjects(ids)
      await invalidateProjectDashboard()
    } finally {
      setBatchDeleting(false)
      setBatchDeleteConfirm(false)
      setBatchDeleteTargets([])
      exitSelectionMode()
    }
  }

  useEffect(() => {
    if (selectionMode) {
      setSelectedIds(new Set())
    }
  }, [selectionMode, selectionResetKey])

  return {
    deleteConfirm,
    deleting,
    selectionMode,
    selectedIds,
    selectableProjects,
    batchDeleteConfirm,
    batchDeleting,
    batchDeleteTargets,
    setDeleteConfirm,
    setBatchDeleteConfirm,
    toggleSelectionMode,
    toggleSelect,
    toggleSelectAll,
    openBatchDeleteConfirm,
    handleDeleteProject,
    handleBatchDelete,
  }
}
