/**
 * 职责: 判断当前用户是否需要展示欢迎信弹窗。
 * 不做: 不负责渲染、API 调用或持久化任何状态。
 */

interface WelcomeLetterUserState {
  welcomeLetterAcknowledgedAt?: string | null
}

/**
 * 仅当用户已加载（非 null）且尚未确认欢迎信时返回 true。
 * 旧缓存缺少字段（空对象）等价于未确认。
 */
export function shouldShowWelcomeLetter(
  user: WelcomeLetterUserState | null,
): boolean {
  return user !== null && !user.welcomeLetterAcknowledgedAt
}
