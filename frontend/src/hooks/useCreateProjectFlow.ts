import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectStore, type Project } from '@/stores/projectStore'
import { useAuthStore } from '@/stores/authStore'
import { useCreateDialogStore } from '@/stores/createDialogStore'
import { useInvalidateProjectDashboard } from './useProjectDashboard'
import {
  getUploadRoute,
  type ProjectTypeSelection,
  type CreateStep,
} from '@/pages/projectList/types'

export function useCreateProjectFlow() {
  const navigate = useNavigate()
  const { projects, createProject } = useProjectStore()
  const { user } = useAuthStore()
  const { isOpen, presetType, close } = useCreateDialogStore()
  const invalidateProjectDashboard = useInvalidateProjectDashboard()

  const [createStep, setCreateStep] = useState<CreateStep>('type')
  const [selectedType, setSelectedType] = useState<ProjectTypeSelection>('bid_review')
  const [newProjectName, setNewProjectName] = useState('')
  const [duplicateConfirm, setDuplicateConfirm] = useState<string | null>(null)
  const [pendingType, setPendingType] = useState<ProjectTypeSelection>('bid_review')

  const resetState = useCallback(() => {
    setNewProjectName('')
    setDuplicateConfirm(null)
  }, [])

  const handleOpen = useCallback(() => {
    if (presetType) {
      setSelectedType(presetType)
      setCreateStep('name')
    } else {
      setCreateStep('type')
      setSelectedType('bid_review')
    }
    setNewProjectName('')
  }, [presetType])

  const handleClose = useCallback(() => {
    close()
    resetState()
  }, [close, resetState])

  const handleTypeSelect = useCallback((type: ProjectTypeSelection) => {
    setSelectedType(type)
    setCreateStep('name')
  }, [])

  const handleCreate = useCallback(async () => {
    if (!newProjectName.trim()) return

    const hasDuplicate = (projects as Project[]).some(
      (p) => p.ownerId === user?.id && p.name.trim() === newProjectName.trim()
    )

    if (hasDuplicate) {
      setPendingType(selectedType)
      close()
      setDuplicateConfirm(newProjectName.trim())
      return
    }

    try {
      const newProject = await createProject(newProjectName.trim(), selectedType)
      await invalidateProjectDashboard()
      handleClose()
      navigate(getUploadRoute(newProject.id, selectedType))
    } catch {
      // store handles errors
    }
  }, [newProjectName, projects, user?.id, selectedType, close, createProject, invalidateProjectDashboard, handleClose, navigate])

  const handleConfirmDuplicate = useCallback(async () => {
    if (!duplicateConfirm) return
    try {
      const newProject = await createProject(duplicateConfirm, pendingType)
      await invalidateProjectDashboard()
      setDuplicateConfirm(null)
      setNewProjectName('')
      navigate(getUploadRoute(newProject.id, pendingType))
    } catch {
      // store handles errors
    }
  }, [duplicateConfirm, pendingType, createProject, invalidateProjectDashboard, navigate])

  const handleCancelDuplicate = useCallback(() => {
    setDuplicateConfirm(null)
    setCreateStep('name')
    useCreateDialogStore.getState().open(pendingType)
  }, [pendingType])

  return {
    isOpen,
    createStep,
    selectedType,
    newProjectName,
    duplicateConfirm,

    handleOpen,
    handleClose,
    handleTypeSelect,
    setNewProjectName,
    handleCreate,
    handleConfirmDuplicate,
    handleCancelDuplicate,
    setCreateStep,
  }
}
