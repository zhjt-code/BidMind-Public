/**
 * 职责: 结果页左栏/中栏宽度百分比的钳制与拖拽换算
 * 不做: 不持有 React 状态、不持久化、不渲染栏内内容
 */

export const RESULT_MIDDLE_PANE_DEFAULT_PERCENT = 30
export const RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT = 28
export const RESULT_MIDDLE_PANE_MIN_PERCENT = 16
export const RESULT_MIDDLE_PANE_MAX_PERCENT = 55

export const RESULT_LEFT_PANE_DEFAULT_PERCENT = 20
export const RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT = 22
export const RESULT_LEFT_PANE_MIN_PERCENT = 12
export const RESULT_LEFT_PANE_MAX_PERCENT = 40

export function clampPanePercent(percent: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, percent))
}

export function computePanePercent(
  clientX: number,
  paneLeft: number,
  parentWidth: number,
  min: number,
  max: number,
  fallback: number,
): number {
  if (parentWidth <= 0) return fallback
  return clampPanePercent(((clientX - paneLeft) / parentWidth) * 100, min, max)
}

export function clampMiddlePanePercent(percent: number): number {
  return clampPanePercent(
    percent,
    RESULT_MIDDLE_PANE_MIN_PERCENT,
    RESULT_MIDDLE_PANE_MAX_PERCENT,
  )
}

export function computeMiddlePanePercent(
  clientX: number,
  paneLeft: number,
  parentWidth: number,
): number {
  return computePanePercent(
    clientX,
    paneLeft,
    parentWidth,
    RESULT_MIDDLE_PANE_MIN_PERCENT,
    RESULT_MIDDLE_PANE_MAX_PERCENT,
    RESULT_MIDDLE_PANE_DEFAULT_PERCENT,
  )
}

export function clampLeftPanePercent(percent: number): number {
  return clampPanePercent(
    percent,
    RESULT_LEFT_PANE_MIN_PERCENT,
    RESULT_LEFT_PANE_MAX_PERCENT,
  )
}

export function computeLeftPanePercent(
  clientX: number,
  paneLeft: number,
  parentWidth: number,
): number {
  return computePanePercent(
    clientX,
    paneLeft,
    parentWidth,
    RESULT_LEFT_PANE_MIN_PERCENT,
    RESULT_LEFT_PANE_MAX_PERCENT,
    RESULT_LEFT_PANE_DEFAULT_PERCENT,
  )
}
