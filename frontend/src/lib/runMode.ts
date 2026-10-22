export type RunMode = 'guided' | 'express'

export const DEFAULT_RUN_MODE: RunMode = 'guided'

export const RUN_MODE_EDITABLE_STATUSES = new Set(['created', 'uploading', 'parsing'])

export function isExpressRunMode(mode?: string | null): boolean {
  return mode === 'express'
}

export function normalizeRunMode(mode?: string | null): RunMode {
  return mode === 'express' ? 'express' : 'guided'
}

export function isRunModeLocked(status?: string | null): boolean {
  if (!status) return false
  return !RUN_MODE_EDITABLE_STATUSES.has(status)
}

/** 页头推进方式只在上传选择态展示；处理进度与后续态隐藏。 */
export function isRunModeChooserVisible(isChooserIdle: boolean): boolean {
  return isChooserIdle
}
