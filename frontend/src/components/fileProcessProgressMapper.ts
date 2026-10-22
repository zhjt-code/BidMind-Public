import type { ProcessStatusData } from '../services/documentApi.ts'
import type { ProgressStep } from '../hooks/useProgressAnimation.ts'
import {
  normalizeOverallStatus,
  normalizeStepStatus,
} from '../services/progressStatusTransforms.ts'

const PROCESS_INITIAL_STEPS: ProgressStep[] = [
  { id: 'parsing', name: 'PDF解析', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'vl_recognition', name: 'VL图片识别', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'chunking', name: '文本分块', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'vectorizing', name: '向量化存储', status: 'pending', progress: 0, targetProgress: 0 },
]

export interface ProcessStatusDisplay {
  steps: ProgressStep[]
  overallTarget: number
  stageText: string
}

export function buildProcessInitialSteps(vlSkipped = false): ProgressStep[] {
  return vlSkipped
    ? PROCESS_INITIAL_STEPS.filter((step) => step.id !== 'vl_recognition')
    : PROCESS_INITIAL_STEPS
}

function getStageByKeyOrIndex(status: ProcessStatusData, step: ProgressStep, index: number) {
  const byKey = status.stages.find((s) => s.key === step.id)
  return byKey ?? status.stages[index]
}

// fallback_reason 形态归一化为可读短语。Reason 由后端用 "+" 连接,
// 这里只取第一段做映射, 避免长串把 UI 撑爆。
const FALLBACK_REASON_LABEL: Record<string, string> = {
  stick_to_cpu: 'GPU 粘滞 CPU',
  task_sticky_cpu: 'GPU 粘滞 CPU',
  gpu_admission_insufficient_vram: '显存不足',
  repeated_admission_fail: '多次准入失败',
  device_cooldown: 'GPU 冷却中',
  orphan_running: '前批未完成',
  stall: 'GPU 失速',
  oom: '显存溢出',
}

function humanFallbackReason(raw: string | undefined): string {
  const first = String(raw || '').split('+')[0] || ''
  return FALLBACK_REASON_LABEL[first] || 'GPU 不可用'
}

function buildVlRecognitionDetail(status: ProcessStatusData): string {
  const recognized = Math.min(status.recognized ?? 0, status.total)
  const processed = Math.min(
    Math.max(status.vl_round_done ?? status.attempted ?? recognized, 0),
    status.total,
  )
  const vlPhase = status.vl_phase
  const retryPending = Math.max(
    status.vl_retry_pending ?? status.failed_pending ?? (status.total - recognized),
    0,
  )
  const finalUnrecognized = Math.max(
    status.vl_unrecognized_final ?? status.unrecognized_images ?? 0,
    0,
  )

  if (vlPhase === 'round1_done') {
    return `已处理 ${processed}/${status.total}，成功识别 ${recognized}，${retryPending} 张待重试`
  }
  if (vlPhase === 'retry_running') {
    return `已处理 ${processed}/${status.total}，成功识别 ${recognized} · 重试中`
  }
  if (vlPhase === 'done') {
    if (finalUnrecognized > 0 || status.manual_review_required) {
      return `识别完成，未识别 ${finalUnrecognized} 张(需人工审核)`
    }
    return `识别完成 (${recognized}/${status.total})`
  }
  return `已处理 ${processed}/${status.total}，成功识别 ${recognized}`
}

export function mapProcessStatusForDisplay(status: ProcessStatusData): ProcessStatusDisplay {
  const baseSteps = buildProcessInitialSteps(Boolean(status.vl_skipped))

  const steps: ProgressStep[] = baseSteps.map((init, i) => {
    const api = getStageByKeyOrIndex(status, init, i)
    if (!api) return init

    const normalized = normalizeStepStatus(api.status)

    let targetProgress = 0
    if (normalized === 'completed') {
      targetProgress = 1
    } else if (api.total && api.total > 0) {
      targetProgress = Math.min((api.progress || 0) / api.total, 1)
    }

    let detail: string | undefined
    if (normalized === 'processing') {
      if (
        init.id === 'vl_recognition' &&
        status.stage === 'vl_recognition' &&
        status.total > 0
      ) {
        detail = buildVlRecognitionDetail(status)
      } else if (init.id === 'chunking' && api.detail) {
        // 后端透传的 chunking 子步骤文案 (例如 "目录解析完成"),
        // 优先级高于 numeric 进度 -- chunking 阶段 numeric 进度通常为空,
        // 不应覆盖后端的可观测子步骤。
        detail = api.detail
      } else if (api.total && api.total > 0) {
        detail = `${api.progress || 0}/${api.total}`
      }
    }

    // 向量化步骤追加 CPU 降级徽标, 让前端能感知 "慢但还在动"。
    if (
      normalized === 'processing' &&
      init.id === 'vectorizing' &&
      status.degraded_to_cpu
    ) {
      const human = humanFallbackReason(status.fallback_reason)
      detail = `${detail || '0/0'} · CPU降级运行中 (${human})`
    }

    return {
      id: init.id,
      name: api.label || init.name,
      status: normalized,
      progress: 0,
      targetProgress,
      detail,
    }
  })

  const completedCount = steps.filter((step) => step.status === 'completed').length
  const processingStep = steps.find((step) => step.status === 'processing')
  const processingContrib = processingStep ? processingStep.targetProgress / steps.length : 0
  const overallTarget = Math.min(completedCount / steps.length + processingContrib, 1)

  const normalizedTopStatus = normalizeOverallStatus(status.status)
  let stageText = ''
  if (normalizedTopStatus === 'completed') {
    stageText = '处理完成'
  } else if (processingStep) {
    const idx = steps.indexOf(processingStep) + 1
    stageText = `步骤${idx} ${processingStep.name}`
    if (processingStep.detail) stageText += ` · ${processingStep.detail}`
  }

  return { steps, overallTarget, stageText }
}
