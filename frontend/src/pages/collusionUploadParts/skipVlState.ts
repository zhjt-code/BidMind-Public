/**
 * 串标上传页"跳过图片识别"偏好持久化（CollusionUpload 私有）。
 *
 * 与 writing/writingUploadVlState.ts 同源实现，按 projectId + 文件类型用
 * sessionStorage 隔离存储用户偏好。修复 ：
 * 1. CollusionUpload 在文件处理完成后会自动 navigate 到进度页，组件卸载
 * 2. 用户从进度页返回 / 浏览器后退 / 路由再次匹配 → 组件重新挂载
 * 3. useState 默认值再次生效（招标默认 true / 投标默认 false），用户显式偏好被覆盖
 *
 * 选择 sessionStorage 而非 localStorage：
 * - 偏好只需在当前会话内保持一致（跨页面跳转、刷新可保留）
 * - tab 关闭后自动清理，避免污染下次会话默认值
 * - 与 writing 模块保持一致，便于后续维护对齐
 */

export type CollusionSkipVlTarget = 'tender' | 'bid'

export function getCollusionSkipVlStorageKey(
  projectId: number,
  target: CollusionSkipVlTarget,
): string {
  return `collusion-upload-skip-vl:${projectId}:${target}`
}

export function readPersistedCollusionSkipVl(
  projectId: number,
  target: CollusionSkipVlTarget,
): boolean | null {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return null
  const raw = window.sessionStorage.getItem(getCollusionSkipVlStorageKey(projectId, target))
  if (raw == null) return null
  return raw === '1'
}

export function persistCollusionSkipVl(
  projectId: number,
  target: CollusionSkipVlTarget,
  value: boolean,
): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.setItem(getCollusionSkipVlStorageKey(projectId, target), value ? '1' : '0')
}

export function clearPersistedCollusionSkipVl(
  projectId: number,
  target: CollusionSkipVlTarget,
): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.removeItem(getCollusionSkipVlStorageKey(projectId, target))
}
