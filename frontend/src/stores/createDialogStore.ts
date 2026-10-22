import { create } from 'zustand'
import type { ProjectTypeSelection } from '@/pages/projectList/types'

interface CreateDialogStore {
  isOpen: boolean
  presetType: ProjectTypeSelection | null
  open: (presetType?: ProjectTypeSelection) => void
  close: () => void
}

export const useCreateDialogStore = create<CreateDialogStore>((set) => ({
  isOpen: false,
  presetType: null,
  open: (presetType) =>
    set({ isOpen: true, presetType: presetType ?? null }),
  close: () =>
    set({ isOpen: false, presetType: null }),
}))
