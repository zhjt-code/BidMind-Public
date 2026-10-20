/**
 * 职责: 把审标 express 的提取/确认/审查状态映射为进度页步骤
 * 不做: 不发请求、不启动提取
 */
import type { CommonStatus } from '@/components/GenericProgressPage'
import type { ProgressStep } from '@/hooks/useProgressAnimation'

export const EXPRESS_REVIEW_STEPS: ProgressStep[] = [
  { id: 'tender-extract', name: '提取招标要求', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'auto-confirm', name: '按默认确认', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'extract', name: '投标响应提取', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'match', name: '响应装配校验', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'review', name: 'AI对比审查', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'report', name: '结果准备', status: 'pending', progress: 0, targetProgress: 0 },
]

interface ExtractStatusLike {
  status?: string
  overall_status?: string
  overall_progress?: number
  progress?: number
  message?: string
  steps?: Array<{ label: string; status: string; progress: number; detail?: string }>
  error_message?: string
}

function step(
  label: string,
  status: string,
  progress: number,
  detail?: string,
): CommonStatus['steps'][number] {
  return { label, status, progress, detail }
}

export function mapPreReviewExpressStatus(
  projectStatus: string,
  extract: ExtractStatusLike,
): CommonStatus {
  const extractProgress = extract.overall_progress ?? extract.progress ?? 0
  const extractRunning = projectStatus === 'extracting' || extract.status === 'running' || extract.overall_status === 'running'
  const extractFailed = extract.status === 'failed' || extract.overall_status === 'failed' || Boolean(extract.error_message)
  const extractDone = projectStatus === 'extracted' || extract.status === 'completed' || extract.overall_status === 'completed'

  const extractStep = extractFailed
    ? step('提取招标要求', 'failed', extractProgress, extract.error_message || extract.message)
    : extractDone
      ? step('提取招标要求', 'completed', 100)
      : extractRunning
        ? step('提取招标要求', 'running', extractProgress, extract.message)
        : step('提取招标要求', 'waiting', 0)

  const confirmStep = extractDone && !extractFailed
    ? step('按默认确认', 'running', 10, '正在按默认确认提取项')
    : step('按默认确认', 'waiting', 0)

  return {
    overallStatus: extractFailed ? 'failed' : 'running',
    overallProgress: extractDone ? 28 : Math.min(24, Math.round(extractProgress * 0.24)),
    steps: [
      extractStep,
      confirmStep,
      step('投标响应提取', 'waiting', 0),
      step('响应装配校验', 'waiting', 0),
      step('AI对比审查', 'waiting', 0),
      step('结果准备', 'waiting', 0),
    ],
    message: extract.message,
    errorMessage: extract.error_message,
  }
}

export function prependCompletedExtractSteps(review: CommonStatus): CommonStatus {
  return {
    ...review,
    steps: [
      step('提取招标要求', 'completed', 100),
      step('按默认确认', 'completed', 100),
      ...review.steps,
    ],
  }
}

export function shouldBootstrapReviewExtract(status?: string | null): boolean {
  return status === 'parsed' || status === 'extracting' || status === 'extracted'
}
