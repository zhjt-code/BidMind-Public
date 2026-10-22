/**
 * 职责: 文档处理节奏(加速 / 完整识别)与 skip_vl 布尔值互转
 * 不做: 不渲染 UI、不持久化、不发请求
 *
 * skip_vl=true 表示跳过图片识别(加速)。界面用正向二分选择，
 * 避免「默认勾选跳过」的双重否定。
 */

export type DocumentPace = 'fast' | 'full'

export const DOCUMENT_PACE_OPTIONS: ReadonlyArray<{
  value: DocumentPace
  label: string
}> = [
  { value: 'fast', label: '加速' },
  { value: 'full', label: '完整识别' },
]

/** 区块标题已标明招标/投标，控件不再重复「xx文件」 */
export const IMAGE_PACE_LABEL = '图片识别'

export const TENDER_IMAGE_PACE_HINT = '扫描件、盖章页请改选完整识别。'

export const BID_IMAGE_PACE_HINT = '证书、执照、人员表请用完整识别。'

export function documentPaceFromSkipVl(skipVl: boolean): DocumentPace {
  return skipVl ? 'fast' : 'full'
}

export function skipVlFromDocumentPace(pace: DocumentPace): boolean {
  return pace === 'fast'
}
