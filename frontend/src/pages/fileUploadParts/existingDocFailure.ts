/**
 * 职责: 已有文档失败态的本地标注与展示文案
 * 不做: 不发起轮询/上传，不决定页面模式
 */
import { resolveDocumentProcessFailureMessage } from '../../lib/formatFailureMessage.ts'

export interface ExistingDocumentFailurePatch {
  parsed: -1
  lastError: string
}

export function toExistingDocumentFailure(
  errorMessage: string | null | undefined,
): ExistingDocumentFailurePatch {
  return {
    parsed: -1,
    lastError: resolveDocumentProcessFailureMessage(errorMessage),
  }
}

export function formatExistingDocumentStatusLabel(parsed: number): string {
  if (parsed === 1) return '已处理'
  if (parsed === -1) return '处理失败'
  return '处理中...'
}
