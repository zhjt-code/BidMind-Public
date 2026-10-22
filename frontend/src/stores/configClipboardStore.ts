import { create } from 'zustand'
import type { ModelPurpose } from '@/services/settingsApi'

export interface ConfigClipboardItem {
  sourceConfigId: string
  sourcePurpose: ModelPurpose
  sourceConfigType: 'local' | 'cloud'
  modelName: string
  copiedAt: number
}

interface ConfigClipboardStore {
  item: ConfigClipboardItem | null
  copy: (i: Omit<ConfigClipboardItem, 'copiedAt'>) => void
  clear: () => void
}

export const useConfigClipboardStore = create<ConfigClipboardStore>((set) => ({
  item: null,
  copy: (i) => set({ item: { ...i, copiedAt: Date.now() } }),
  clear: () => set({ item: null }),
}))
