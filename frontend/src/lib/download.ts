const ILLEGAL_FILENAME_CHARS = /[<>:"/\\|?*\u0000-\u001f\u200b-\u200f\ufeff\u202a-\u202e]/g
const WINDOWS_RESERVED = new Set([
  'CON', 'PRN', 'AUX', 'NUL',
  'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
  'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
])
const MAX_EXPORT_FILENAME_LEN = 200

/**
 * 按「项目名+流程类型」拼浏览器下载文件名。
 * 职责: 与后端 build_export_filename 对齐导出命名。
 * 不做: 不发起下载、不解析 Content-Disposition。
 */
export function buildExportFilename(
  projectName: string | null | undefined,
  label: string,
  ext: string,
  fallback = '未命名项目',
): string {
  const normalizedExt = (ext || '').replace(/^\./, '')
  let stem = (projectName ?? '').trim() || fallback
  stem = stem.replace(ILLEGAL_FILENAME_CHARS, '_').replace(/^[. ]+|[. ]+$/g, '')
  if (!stem) stem = fallback
  const stemUpper = (stem.includes('.') ? stem.slice(0, stem.indexOf('.')) : stem).toUpperCase()
  if (WINDOWS_RESERVED.has(stemUpper)) {
    stem = `_${stem}`
  }
  let name = normalizedExt ? `${stem}${label}.${normalizedExt}` : `${stem}${label}`
  if (name.length > MAX_EXPORT_FILENAME_LEN) {
    if (normalizedExt) {
      const keep = MAX_EXPORT_FILENAME_LEN - normalizedExt.length - 1
      name = `${name.slice(0, Math.max(keep, 1))}.${normalizedExt}`
    } else {
      name = name.slice(0, MAX_EXPORT_FILENAME_LEN)
    }
  }
  return name
}

/**
 * Trigger a browser file download from a Blob response.
 * Creates a temporary object URL and anchor element, then cleans up.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
}
