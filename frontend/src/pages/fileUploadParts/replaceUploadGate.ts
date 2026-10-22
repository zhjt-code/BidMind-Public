/**
 * 职责: 判定单侧更换上传按钮是否可点
 * 不做: 不发起上传/解析
 */
import type { BidReviewReplaceSlot } from './uploadPageState'

export interface ReplaceUploadFileState {
  status?: string | null
}

export function canSubmitReplaceUpload(
  replacingSlot: BidReviewReplaceSlot | null | undefined,
  replacingFile: ReplaceUploadFileState | null | undefined,
  isBusy: boolean,
): boolean {
  if (!replacingSlot || isBusy) return false
  return replacingFile?.status === 'pending'
}
