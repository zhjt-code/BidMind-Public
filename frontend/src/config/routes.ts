export const PUBLIC_HOME = '/'
export const APP_HOME = '/app'
export const LOGIN_PATH = '/login'
export const REGISTER_PATH = '/register'

/** 用户主动点击「退出登录」后的落地页；会话失效/401 仍应使用 LOGIN_PATH */
export const VOLUNTARY_LOGOUT_PATH = PUBLIC_HOME

export type ProjectListType = 'bid_review' | 'bid_writing' | 'collusion_check'

export function buildAppProjectListPath(projectType?: ProjectListType): string {
  if (!projectType) return APP_HOME
  return `${APP_HOME}?type=${projectType}`
}
