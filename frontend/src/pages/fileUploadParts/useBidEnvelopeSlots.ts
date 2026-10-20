/**
 * 职责: 投标信封上传槽位状态容器——模式选择 + 按槽位键的文件/已有文档/进度/拖拽状态
 * 不做: 不发请求、不轮询、不做页面级导航或门禁决策
 *
 * 槽位 id 约定: 招标固定 'tender'; 投标侧与信封档案槽位同名
 * (整本 'integral', 分卷 'commercial'/'technical'), 与 documents.bid_part 对应。
 */
import { useCallback, useMemo, useState } from 'react'

import type { ProcessStatusData } from '@/services/documentApi'
import {
  DEFAULT_ENVELOPE_MODE,
  getEnvelopeProfile,
} from '@/lib/bidEnvelope'
import type {
  EnvelopeUploadedFile,
  ExistingDocument,
} from './UploadSlotArea'

export interface UploadSlotDescriptor {
  id: string
  docType: 'tender' | 'bid'
  /** 上传时提交的 bid_part(招标与整本为 null) */
  bidPart: string | null
  label: string
  /** 拖拽提示中的文档名 */
  docName: string
}

export const TENDER_SLOT: UploadSlotDescriptor = {
  id: 'tender',
  docType: 'tender',
  bidPart: null,
  label: '招标文件 (PDF/Word)',
  docName: '招标文件',
}

const BID_DOC_NAMES: Record<string, string> = {
  integral: '投标文件',
  commercial: '商务标',
  technical: '技术标',
}

/** 当前模式下的投标侧槽位描述(档案数据驱动, 新形态只需扩档案) */
export function bidSlotDescriptors(mode: string): UploadSlotDescriptor[] {
  return getEnvelopeProfile(mode).slots.map((slot) => ({
    id: slot.id,
    docType: 'bid' as const,
    bidPart: slot.bidPart,
    label: slot.label,
    docName: BID_DOC_NAMES[slot.id] || slot.label,
  }))
}

export interface SlotState {
  file: EnvelopeUploadedFile | null
  existing: ExistingDocument | null
  processStatus: ProcessStatusData | null
  existingProcessStatus: ProcessStatusData | null
  dragging: boolean
}

export const EMPTY_SLOT_STATE: SlotState = {
  file: null,
  existing: null,
  processStatus: null,
  existingProcessStatus: null,
  dragging: false,
}

export interface UseBidEnvelopeSlotsReturn {
  mode: string
  setMode: (mode: string) => void
  /** 投标侧槽位(随模式变化) */
  bidSlots: UploadSlotDescriptor[]
  /** 招标 + 投标全部槽位 */
  allSlots: UploadSlotDescriptor[]
  slotState: (slotId: string) => SlotState
  patchSlot: (slotId: string, patch: Partial<SlotState>) => void
  updateSlotFile: (
    slotId: string,
    updater: (prev: EnvelopeUploadedFile | null) => EnvelopeUploadedFile | null,
  ) => void
  updateSlotExisting: (
    slotId: string,
    updater: (prev: ExistingDocument | null) => ExistingDocument | null,
  ) => void
  resetSlots: () => void
}

export function useBidEnvelopeSlots(): UseBidEnvelopeSlotsReturn {
  const [mode, setMode] = useState<string>(DEFAULT_ENVELOPE_MODE)
  const [slotStates, setSlotStates] = useState<Record<string, SlotState>>({})

  const slotState = useCallback(
    (slotId: string): SlotState => slotStates[slotId] ?? EMPTY_SLOT_STATE,
    [slotStates],
  )

  const patchSlot = useCallback(
    (slotId: string, patch: Partial<SlotState>) => {
      setSlotStates((prev) => {
        const current = prev[slotId] ?? EMPTY_SLOT_STATE
        return { ...prev, [slotId]: { ...current, ...patch } }
      })
    },
    [],
  )

  const updateSlotFile = useCallback(
    (
      slotId: string,
      updater: (prev: EnvelopeUploadedFile | null) => EnvelopeUploadedFile | null,
    ) => {
      setSlotStates((prev) => {
        const current = prev[slotId] ?? EMPTY_SLOT_STATE
        return { ...prev, [slotId]: { ...current, file: updater(current.file) } }
      })
    },
    [],
  )

  const updateSlotExisting = useCallback(
    (
      slotId: string,
      updater: (prev: ExistingDocument | null) => ExistingDocument | null,
    ) => {
      setSlotStates((prev) => {
        const current = prev[slotId] ?? EMPTY_SLOT_STATE
        return {
          ...prev,
          [slotId]: { ...current, existing: updater(current.existing) },
        }
      })
    },
    [],
  )

  const resetSlots = useCallback(() => setSlotStates({}), [])

  const bidSlots = useMemo(() => bidSlotDescriptors(mode), [mode])
  const allSlots = useMemo(() => [TENDER_SLOT, ...bidSlots], [bidSlots])

  return {
    mode,
    setMode,
    bidSlots,
    allSlots,
    slotState,
    patchSlot,
    updateSlotFile,
    updateSlotExisting,
    resetSlots,
  }
}
