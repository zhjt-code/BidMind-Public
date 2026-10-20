/**
 * 招标要求提取阶段的进度展示页 (loading || extracting 分支)。
 *
 * 入参 extracting=true 时展示完整 ProgressPanel,
 * extracting=false 时展示 spinner loading 占位,与原 RequirementReview 双分支
 * byte-equal。
 *
 * accentColor='blue' 锁定为投标审查业务域主题色 (ui.mdc §1 进度条规范)。
 */
import React from 'react'
import ProgressPanel from '@/components/ProgressPanel'
import type { ProgressStep } from '@/hooks/useProgressAnimation'

export interface ExtractionProgressPanelProps {
  /** 当前是否处于提取轮询中 (false 时显示 loading spinner) */
  extracting: boolean
  steps: ProgressStep[]
  displayProgress: number
  stageText: string
  isInitialLoading: boolean
  error: string | null
}

export const ExtractionProgressPanel: React.FC<ExtractionProgressPanelProps> = ({
  extracting,
  steps,
  displayProgress,
  stageText,
  isInitialLoading,
  error,
}) => {
  if (!extracting) {
    return (
      <div className="p-8">
        <div className="max-w-2xl mx-auto flex flex-col items-center justify-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mb-4" />
          <p className="text-gray-500">加载中...</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">招标要求提取</h1>
        <p className="text-gray-500 mt-1">核验、合并与评分提取通常较久，完成后进入招标要求确认。</p>
      </div>

      <div className="max-w-2xl mx-auto">
        <ProgressPanel
          steps={steps}
          displayProgress={displayProgress}
          stageText={stageText}
          isRunning={true}
          isCompleted={false}
          isInitialLoading={isInitialLoading}
          error={error}
          accentColor="blue"
        />
      </div>
    </div>
  )
}
