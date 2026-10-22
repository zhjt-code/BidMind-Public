/**
 * 批处理状态 → 进度面板步骤映射。
 *
 * 历史教训：完成态必须依赖后端显式终态 phase==='done'。
 *   idle 仅代表"任务已静默"，不等同于完成（防止刚打开页面误判为已完成）。
 *   image_classify / label_resolve 都被前端视为 tagging 步骤，确保
 *   索引阶段之后用户仍能看到"打标分类"步骤在跑。
 *
 * 本模块为无副作用纯函数，便于 node:test 单测覆盖。
 */

import type { MaterialBatchStatus } from '@/services/writingApi'
import type { ProgressStep } from '@/hooks/useProgressAnimation'
import { BATCH_STEPS } from './constants.ts'
import { formatFailureMessage } from '../../../lib/formatFailureMessage.ts'

export interface BatchStatusMapping {
  steps: ProgressStep[]
  overallTarget: number
  stageText: string
}

/**
 * 后端 phase 到前端步骤 id 的映射表。
 *
 * image_classify / label_resolve 都收敛到 tagging 步骤（避免后台
 * image_classify / tagging 期间被误判为已完成）。
 */
const PHASE_TO_STEP_ID: Record<string, string> = {
  parsing: 'parsing',
  vl_recognition: 'vl_recognition',
  indexing: 'indexing',
  image_classify: 'tagging',
  label_resolve: 'tagging',
  tagging: 'tagging',
}

/**
 * 将 MaterialBatchStatus 映射为：steps / overallTarget / stageText。
 *
 * 行为契约与原 WritingMaterials.tsx::mapBatchStatusToSteps 完全一致。
 */
export function mapBatchStatusToSteps(status: MaterialBatchStatus): BatchStatusMapping {
  const isDone = status.phase === 'done'
  const isFailed = status.phase === 'failed'
  const activeStepId = isDone || isFailed ? undefined : PHASE_TO_STEP_ID[status.phase] || 'parsing'

  const stepOrder = BATCH_STEPS.map((s) => s.id)
  const activeStepIdx = activeStepId ? stepOrder.indexOf(activeStepId) : -1

  const steps: ProgressStep[] = BATCH_STEPS.map((init, idx) => {
    let stepStatus: ProgressStep['status'] = 'pending'
    let targetProgress = 0
    let detail: string | undefined

    if (isDone) {
      stepStatus = 'completed'
      targetProgress = 1
    } else if (activeStepIdx >= 0 && idx < activeStepIdx) {
      stepStatus = 'completed'
      targetProgress = 1
    } else if (activeStepIdx >= 0 && idx === activeStepIdx) {
      stepStatus = 'processing'
      if (init.id === 'parsing') {
        targetProgress = 0.2
      } else if (init.id === 'vl_recognition') {
        if (status.vlProgress) {
          const parts = status.vlProgress.split('/')
          const done = Number(parts[0]) || 0
          const vlTotal = Number(parts[1]) || 0
          targetProgress = vlTotal > 0 ? Math.min(done / vlTotal, 0.95) : 0
          detail = `已识别 ${status.vlProgress}`
        }
      } else if (init.id === 'indexing') {
        const total = status.total + status.completed + status.failed
        targetProgress = total > 0 ? Math.min((status.completed + status.failed) / total, 0.95) : 0.5
      } else if (init.id === 'tagging') {
        if (status.phase === 'image_classify') {
          targetProgress = 0.3
          detail = '图片分类与结构化'
        } else if (status.phase === 'label_resolve' || status.heartbeatPhase === 'label_resolve') {
          targetProgress = 0.6
          detail = '标签交叉校验'
        } else {
          targetProgress = 0.85
          detail = '素材打标与分类'
        }
      }
    }

    return { ...init, status: stepStatus, targetProgress, detail }
  })

  const completedCount = steps.filter((s) => s.status === 'completed').length
  const processingStep = steps.find((s) => s.status === 'processing')
  const processingContrib = processingStep ? processingStep.targetProgress / steps.length : 0
  const overallTarget =
    isDone || completedCount === steps.length
      ? 1
      : Math.min(completedCount / steps.length + processingContrib, 1)

  let stageText = ''
  if (isDone) {
    stageText = '全部处理完成'
  } else if (isFailed) {
    const reason = status.lastError?.trim()
      ? formatFailureMessage(status.lastError)
      : ''
    stageText = reason ? `处理失败: ${reason}` : '处理失败'
  } else if (processingStep) {
    const idx = steps.indexOf(processingStep) + 1
    stageText = `步骤${idx} ${processingStep.name}`
    if (processingStep.detail) stageText += ` · ${processingStep.detail}`
  } else if (status.taskActive) {
    stageText = '任务恢复中'
  }

  return { steps, overallTarget, stageText }
}
