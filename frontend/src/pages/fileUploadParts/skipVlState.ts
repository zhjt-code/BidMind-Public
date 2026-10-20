/**
 * 投标审查上传页"跳过图片识别"偏好持久化（FileUpload 私有）。
 *
 * 与 collusionUploadParts/skipVlState.ts、writing/writingUploadVlState.ts
 * 三处同源实现。修复 同源扩散：
 * - FileUpload 在文件处理完成后会自动 navigate 到 /requirements（投标审查
 * 唯一的人工审核节点，用户经常返回上传页）
 * - 返回时组件重新挂载，useState(true) 默认值覆盖用户的"取消勾选"
 *
 * 注意：仅对 tender 文件的 skipVl 偏好持久化（FileUpload 中 bid 文件不暴露此
 * 开关，由 L309 强制 false 走完整识别）。key 命名为 file-upload-skip-vl，与
 * collusion/writing 隔离。
 */

export function getFileUploadSkipVlStorageKey(projectId: number): string {
  return `file-upload-skip-vl:${projectId}`
}

export function readPersistedFileUploadSkipVl(projectId: number): boolean | null {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return null
  const raw = window.sessionStorage.getItem(getFileUploadSkipVlStorageKey(projectId))
  if (raw == null) return null
  return raw === '1'
}

export function persistFileUploadSkipVl(projectId: number, value: boolean): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.setItem(getFileUploadSkipVlStorageKey(projectId), value ? '1' : '0')
}

export function clearPersistedFileUploadSkipVl(projectId: number): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.removeItem(getFileUploadSkipVlStorageKey(projectId))
}
