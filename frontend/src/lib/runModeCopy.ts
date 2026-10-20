import type { RunMode } from '@/lib/runMode'

export const RUN_MODE_OPTIONS = [
  { value: 'guided', label: '人工确认' },
  { value: 'express', label: '一步到位' },
] as const

export const RUN_MODE_LABEL = '推进方式'

export const EXPRESS_QUALITY_WARNING =
  '不建议“一步到位”处理业务，缺少人工确认会明显降低结果质量。'

export const RUN_MODE_BASE_HINT =
  '人工确认会在中间节点停下来核对；一步到位按系统默认连续推进。'

const REVIEW_EXPRESS_RISKS = [
  '将按提取结果确认全部可审查项与评分项；参考/风险折叠项仍不进入审查。',
  '不停留确认页，无法改写、删除或升降级提取项，也不配置知识库。',
  '提取质量告警不会阻断开跑；提取与审查计费将连续发生。',
]

const WRITING_EXPRESS_RISKS = [
  '约束与评分项按提取结果确认，目录按生成结果，不人工改结构。',
  '不上传素材、不引用知识库；绑定按匹配结果确认，未匹配约束将忽略。',
  '提取、目录、匹配、撰写计费将连续发生；正文质量通常明显低于人工确认。',
]

export function getExpressRiskLines(projectType?: string): string[] {
  if (projectType === 'bid_writing') return WRITING_EXPRESS_RISKS
  return REVIEW_EXPRESS_RISKS
}

export function getRunModeFieldHint(runMode: RunMode, projectType?: string): string {
  const parts = [EXPRESS_QUALITY_WARNING, RUN_MODE_BASE_HINT]
  if (runMode === 'express') {
    parts.push(...getExpressRiskLines(projectType))
  }
  return parts.join(' ')
}

export function getPostProcessHint(runMode: RunMode, projectType?: string): string {
  if (runMode !== 'express') {
    return projectType === 'bid_writing'
      ? '处理完成后进入约束确认。'
      : '处理完成后进入招标要求确认。'
  }
  return projectType === 'bid_writing'
    ? '一步到位：提取完成后进入编写进度，中间确认页将跳过。'
    : '一步到位：提取完成后进入审查进度，中间确认页将跳过。'
}

export function getWritingExtractDoneHint(runMode: RunMode): string {
  return runMode === 'express' ? '正在进入编写进度...' : '正在跳转到约束确认页...'
}
