/**
 * 职责: 维护右下角反馈悬浮按钮的会话级显隐，并通知同页其它浮动控件避让。
 * 不做: 不控制反馈面板开闭、不决定回到顶部是否显示。
 */

export const FEEDBACK_FAB_DISMISSED_KEY = 'feedback_btn_dismissed'
export const FEEDBACK_FAB_VISIBILITY_EVENT = 'bidmind:feedback-fab-visibility'

function getSessionStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return window.sessionStorage
  } catch {
    return null
  }
}

export function isFeedbackFabDismissed(): boolean {
  const storage = getSessionStorage()
  if (!storage) return false
  try {
    return storage.getItem(FEEDBACK_FAB_DISMISSED_KEY) === '1'
  } catch {
    return false
  }
}

export function isFeedbackFabVisible(): boolean {
  return !isFeedbackFabDismissed()
}

export function dismissFeedbackFab(): void {
  const storage = getSessionStorage()
  try {
    storage?.setItem(FEEDBACK_FAB_DISMISSED_KEY, '1')
  } catch {
    // private mode / quota — still broadcast so UI can drop the FAB slot
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(FEEDBACK_FAB_VISIBILITY_EVENT))
  }
}

/** 反馈 FAB 占位时抬高，关闭后落回底部槽位 */
export function resolveBackToTopBottomClass(
  feedbackFabVisible: boolean,
): 'bottom-24' | 'bottom-6' {
  return feedbackFabVisible ? 'bottom-24' : 'bottom-6'
}
