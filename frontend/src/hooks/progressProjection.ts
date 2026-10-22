import type { ProgressStep } from './useProgressAnimation'

export interface ProjectedProgressSnapshot {
  steps: ProgressStep[]
  displayProgress: number
}

/**
 * 职责: 将后端进度快照同步转换为组件展示快照。
 * 不做: 不推导业务状态、不缓动、不做单调钳制、不管理轮询。
 */
export function projectProgressSnapshot(
  newSteps: ProgressStep[],
  overallTarget: number,
): ProjectedProgressSnapshot {
  return {
    displayProgress: overallTarget,
    steps: newSteps.map((step) => ({
      ...step,
      progress: step.targetProgress,
    })),
  }
}
