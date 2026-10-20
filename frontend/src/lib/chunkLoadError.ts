/**
 * 职责: 识别浏览器已拒绝的动态 import 失败
 * 不做: 不刷新页面、不解析模块图
 */

const CHUNK_LOAD_RE = /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed/i

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '')
  return CHUNK_LOAD_RE.test(message)
}
