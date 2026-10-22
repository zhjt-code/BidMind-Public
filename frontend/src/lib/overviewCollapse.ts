/**
 * 职责: 判定结果页概览自动让位的几何条件与状态迁移。
 * 不做: 不挂 DOM 监听、不持久化偏好、不区分投标/串标文案。
 */

export const OVERVIEW_SCROLL_SLACK_PX = 16
export const OVERVIEW_BOTTOM_DWELL_MS = 300

export type OverviewCollapseControl = 'auto' | 'manual'

export interface OverviewCollapseState {
  control: OverviewCollapseControl
  collapsed: boolean
}

export type OverviewCollapseEvent =
  | { type: 'detail_engage' }
  | { type: 'inner_bottom_dwell' }
  | { type: 'toggle' }

export const INITIAL_OVERVIEW_COLLAPSE_STATE: OverviewCollapseState = {
  control: 'auto',
  collapsed: false,
}

export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

export function hasOverflow(
  metrics: ScrollMetrics,
  slackPx = OVERVIEW_SCROLL_SLACK_PX,
): boolean {
  return metrics.scrollHeight - metrics.clientHeight > slackPx
}

export function isScrolledToBottom(
  metrics: ScrollMetrics,
  slackPx = OVERVIEW_SCROLL_SLACK_PX,
): boolean {
  return metrics.scrollTop + metrics.clientHeight >= metrics.scrollHeight - slackPx
}

export function canConsumeInnerScroll(
  metrics: ScrollMetrics,
  slackPx = OVERVIEW_SCROLL_SLACK_PX,
): boolean {
  return hasOverflow(metrics, slackPx) && isScrolledToBottom(metrics, slackPx)
}

export function isDwellElapsed(
  startedAt: number | null,
  now: number,
  dwellMs = OVERVIEW_BOTTOM_DWELL_MS,
): boolean {
  return startedAt !== null && now - startedAt >= dwellMs
}

export function reduceOverviewCollapse(
  state: OverviewCollapseState,
  event: OverviewCollapseEvent,
): OverviewCollapseState {
  if (event.type === 'toggle') {
    return { control: 'manual', collapsed: !state.collapsed }
  }
  if (state.collapsed) {
    return state
  }
  if (event.type === 'detail_engage') {
    return { ...state, collapsed: true }
  }
  if (state.control !== 'auto') {
    return state
  }
  return { ...state, collapsed: true }
}

export interface DetailShellLookupRoot {
  parentElement: { nextElementSibling: unknown } | null
}

export function resolveDetailShell(statsRoot: DetailShellLookupRoot): unknown {
  return statsRoot.parentElement?.nextElementSibling ?? null
}
