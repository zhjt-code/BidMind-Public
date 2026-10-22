/**
 * 职责: 判定招标要求确认页首载是否应停留在提取进度视图。
 * 不做: 不发请求、不映射步骤、不改 React 状态。
 */

export function shouldHoldExtractionProgressView(
  extractStatus: string | null | undefined,
  projectStatus: string | null | undefined,
): boolean {
  if (extractStatus === 'running') return true
  return projectStatus === 'extracting'
}
