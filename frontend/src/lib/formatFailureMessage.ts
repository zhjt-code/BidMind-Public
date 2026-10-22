import { sanitizeUserFacingText } from '../services/progressStatusTransforms.ts'

/** 与 backend OFFICE_CONVERT_TIMEOUT_USER_MESSAGE / Gotenberg API_TIMEOUT=180s 对齐 */
export const OFFICE_CONVERT_TIMEOUT_MESSAGE =
  'Word 文档转换超时（超过 3 分钟）。请稍后重试，或先另存为 PDF 再上传。'

/**
 * 职责: 将后端失败原因清洗为用户可读文案
 * 不做: 不发起请求、不推断业务恢复动作
 */
export function formatFailureMessage(message: string | null | undefined): string {
  if (!message || !message.trim()) {
    return '系统未返回具体原因'
  }
  return sanitizeUserFacingText(message)
    .replace(/Word 文档转换失败:\s*HTTP 503/gi, OFFICE_CONVERT_TIMEOUT_MESSAGE)
    .replace(/\bpair\b/gi, '文件对')
    .replace(/\bfailed\b/gi, '分析失败')
    .trim() || '系统未返回具体原因'
}

/** 上传页失败卡片在拿不到后端原因时的引导文案（写标恢复场景） */
export const UPLOAD_FAILURE_FALLBACK = '处理失败，可从失败节点继续恢复'

/** 文档解析失败卡片兜底（投标审查/串标上传） */
export const DOCUMENT_PROCESS_FAILURE_FALLBACK = '处理失败，请更换文件后重试'

/**
 * 写标上传页失败正文：有原因走出口闸门，无原因回落引导文案。
 */
export function resolveUploadFailureMessage(message: string | null | undefined): string {
  if (!message || !message.trim()) {
    return UPLOAD_FAILURE_FALLBACK
  }
  return formatFailureMessage(message)
}

/**
 * 文档处理失败正文：有原因走出口闸门，无原因回落更换文件引导。
 */
export function resolveDocumentProcessFailureMessage(
  message: string | null | undefined,
): string {
  if (!message || !message.trim()) {
    return DOCUMENT_PROCESS_FAILURE_FALLBACK
  }
  return formatFailureMessage(message)
}
