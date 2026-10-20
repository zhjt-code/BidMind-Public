/**
 * 职责: 把写标 express 跨阶段状态映射为一条总进度
 * 不做: 不发请求、不确认约束或绑定
 */
import type { CommonStatus } from '@/components/GenericProgressPage'
import type { ProgressStep } from '@/hooks/useProgressAnimation'
import type { WritingProjectStatus } from '@/services/writingApi'

export const WRITING_EXPRESS_STEPS: ProgressStep[] = [
  { id: 'extract', name: '提取约束', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'outline', name: '生成目录', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'match', name: '匹配绑定', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'compose', name: '生成正文', status: 'pending', progress: 0, targetProgress: 0 },
]

const PHASE_BY_STATUS: Record<string, 'extract' | 'outline' | 'match' | 'compose'> = {
  extracting: 'extract',
  extracted: 'outline',
  outlining: 'outline',
  outlined: 'outline',
  matching: 'match',
  matched: 'match',
  writing: 'compose',
  composed: 'compose',
  formatting: 'compose',
  completed: 'compose',
}

const PHASE_ORDER = ['extract', 'outline', 'match', 'compose'] as const

export function mapWritingExpressStatus(
  projectStatus: string,
  writing: Pick<WritingProjectStatus, 'overallStatus' | 'overallProgress' | 'message' | 'errorMessage' | 'currentDetail'>,
): CommonStatus {
  const phase = PHASE_BY_STATUS[projectStatus] ?? 'extract'
  const failed = projectStatus === 'failed' || writing.overallStatus === 'failed'
  const completed = projectStatus === 'composed' || projectStatus === 'formatting' || projectStatus === 'completed'
  const phaseIndex = PHASE_ORDER.indexOf(phase)
  const currentProgress = failed ? 0 : (writing.overallProgress || (completed ? 100 : 20))

  const steps = WRITING_EXPRESS_STEPS.map((step, index) => {
    const label = step.name
    if (completed) {
      return { label, status: 'completed', progress: 100 }
    }
    if (failed && index === phaseIndex) {
      return { label, status: 'failed', progress: currentProgress, detail: writing.errorMessage || writing.message }
    }
    if (index < phaseIndex) {
      return { label, status: 'completed', progress: 100 }
    }
    if (index === phaseIndex) {
      return {
        label,
        status: failed ? 'failed' : 'running',
        progress: currentProgress,
        detail: writing.currentDetail || writing.message,
      }
    }
    return { label, status: 'waiting', progress: 0 }
  })

  return {
    overallStatus: failed ? 'failed' : completed ? 'completed' : 'running',
    overallProgress: completed ? 100 : Math.min(99, Math.round((phaseIndex * 25) + currentProgress * 0.25)),
    steps,
    message: writing.message,
    errorMessage: writing.errorMessage,
  }
}
