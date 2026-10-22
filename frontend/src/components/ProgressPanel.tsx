import React from 'react'
import type { ProgressStep } from '@/hooks/useProgressAnimation'

interface ProgressPanelProps {
  steps: ProgressStep[]
  displayProgress: number
  stageText: string
  isRunning: boolean
  isCompleted: boolean
  canViewResult?: boolean
  isInitialLoading: boolean
  error: string | null
  accentColor?: 'blue' | 'iris' | 'teal'
  completedMessage?: string
  onViewResult?: () => void
  resultButtonText?: string
  compact?: boolean
  isRecovering?: boolean
  recoveryMessage?: string
}

/**
 * 进度条主题色契约（与 .cursor/rules/modules/ui.mdc 同步）：
 *   blue   = 投标审查（review）   /project/:id/{upload,requirements,progress,result}
 *   iris   = 串标审查（collusion） /project/:id/collusion/*  实色 #9056D8
 *   teal   = 标书编写（writing）   /project/:id/writing/*
 *
 * 注意：本 map 仅用于"处理中"主题色切换。
 * 状态语义色（completed=green / error=red / pending=gray）在 getCircleClass、
 * getBarColor 中按 step.status 直接映射，不在此 map 中。
 * 警告/恢复态使用 amber 辅助色板（见 ProgressPanel 内 isRecovering 提示），
 * 不参与主题色切换。
 */
const COLOR_MAP = {
  blue: {
    bar: 'bg-blue-500',
    barDone: 'bg-green-500',
    spinner: 'border-blue-500',
    processing: 'bg-blue-500',
    processingText: 'text-blue-700',
    resultBtn: 'bg-blue-500 hover:bg-blue-600',
    loadingSpinner: 'border-blue-500',
    stripe: '#3b82f6',
  },
  iris: {
    bar: 'bg-[#9056D8]',
    barDone: 'bg-green-500',
    spinner: 'border-[#9056D8]',
    processing: 'bg-[#9056D8]',
    processingText: 'text-[#6C40A2]',
    resultBtn: 'bg-[#9056D8] hover:bg-[#6C40A2]',
    loadingSpinner: 'border-[#9056D8]',
    stripe: '#9056D8',
  },
  teal: {
    bar: 'bg-teal-500',
    barDone: 'bg-green-500',
    spinner: 'border-teal-500',
    processing: 'bg-teal-500',
    processingText: 'text-teal-700',
    resultBtn: 'bg-teal-500 hover:bg-teal-600',
    loadingSpinner: 'border-teal-500',
    stripe: '#14b8a6',
  },
} as const

function stripeFillStyle(width: string, stripeColor: string): React.CSSProperties {
  return {
    width,
    ['--progress-stripe-color']: stripeColor,
  } as React.CSSProperties
}

const ProgressPanel: React.FC<ProgressPanelProps> = ({
  steps,
  displayProgress,
  stageText,
  isRunning,
  isCompleted,
  canViewResult = false,
  isInitialLoading,
  error,
  accentColor = 'blue',
  completedMessage,
  onViewResult,
  resultButtonText,
  compact = false,
  isRecovering = false,
  recoveryMessage,
}) => {
  const colors = COLOR_MAP[accentColor]

  const getCircleClass = (step: ProgressStep) => {
    switch (step.status) {
      case 'completed':
        return 'bg-green-500'
      case 'processing':
        return colors.processing
      case 'error':
        return 'bg-red-500'
      default:
        return isRunning ? 'bg-gray-300 animate-pulse' : 'bg-gray-300'
    }
  }

  const getBarColor = (status: ProgressStep['status']) => {
    switch (status) {
      case 'completed':
        return 'bg-green-500'
      case 'processing':
        return 'progress-stripe'
      case 'error':
        return 'bg-red-500'
      default:
        return 'bg-gray-300'
    }
  }

  const circleSize = compact ? 'w-8 h-8' : 'w-10 h-10'
  const iconSize = compact ? 'w-5 h-5' : 'w-6 h-6'
  const spinnerSize = compact ? 'h-4 w-4' : 'h-5 w-5'
  const getStepBarWidth = (step: ProgressStep) => {
    if (step.status === 'completed') return '100%'
    if (step.status === 'pending') return '0%'
    return `${step.progress * 100}%`
  }

  return (
    <div className={compact ? '' : 'bg-white rounded-lg shadow p-6'}>
      {isInitialLoading && !compact ? (
        <div className="flex flex-col items-center justify-center py-12">
          <div className={`animate-spin rounded-full h-12 w-12 border-b-2 ${colors.loadingSpinner} mb-4`} />
          <p className="text-gray-500">正在获取状态...</p>
        </div>
      ) : (
        <>
          {/* 整体进度条 */}
          <div className={compact ? 'mb-4 pb-4 border-b' : 'mb-6 pb-6 border-b'}>
            <div className="flex justify-between items-center mb-2">
              <div className="flex items-center gap-2">
                <span className={`font-medium text-gray-700 ${compact ? 'text-sm' : ''}`}>整体进度</span>
                {isRunning && !isCompleted && (
                  <div className={`animate-spin rounded-full h-3.5 w-3.5 border-b-2 ${colors.spinner}`} />
                )}
              </div>
              <span className="text-sm text-gray-500">
                {stageText || `${Math.round(displayProgress * 100)}%`}
              </span>
            </div>
            <div className={`w-full bg-gray-200 rounded-full overflow-hidden ${compact ? 'h-2' : 'h-3'}`}>
              <div
                className={`${compact ? 'h-2' : 'h-3'} rounded-full transition-none ${isCompleted ? colors.barDone : isRunning ? 'progress-stripe' : colors.bar}`}
                style={
                  isCompleted
                    ? { width: '100%' }
                    : isRunning
                      ? stripeFillStyle(`${displayProgress * 100}%`, colors.stripe)
                      : { width: `${displayProgress * 100}%` }
                }
              />
            </div>
          </div>

          {/* 恢复提示 */}
          {isRecovering && (
            <div className="mb-4 p-3 bg-amber-50 rounded-lg border border-amber-200 flex items-start gap-2">
              <svg className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              <p className="text-sm text-amber-800">
                {recoveryMessage || '系统检测到处理异常，正在自动恢复中，已完成的部分将被跳过...'}
              </p>
            </div>
          )}

          {/* 步骤进度 */}
          <div className={compact ? 'space-y-3' : 'space-y-6'}>
            {steps.map((step, index) => (
              <div key={step.id} className={`flex items-center ${compact ? 'space-x-3' : 'space-x-4'}`}>
                <div
                  className={`${circleSize} rounded-full flex items-center justify-center text-white font-bold ${getCircleClass(step)}`}
                >
                  {step.status === 'completed' ? (
                    <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : step.status === 'error' ? (
                    <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  ) : step.status === 'processing' ? (
                    <div className={`animate-spin rounded-full ${spinnerSize} border-2 border-white border-t-transparent`} />
                  ) : (
                    <span className={`text-gray-400 ${compact ? 'text-sm' : ''}`}>{index + 1}</span>
                  )}
                </div>

                <div className="flex-1">
                  <div className="flex justify-between items-center mb-1">
                    <span className={`${compact ? 'text-sm' : ''} font-medium ${step.status === 'processing' ? colors.processingText : 'text-gray-700'}`}>
                      {step.name}
                    </span>
                    <span className={`text-gray-500 ${compact ? 'text-xs' : 'text-sm'}`}>
                      {step.status === 'completed'
                        ? '完成'
                        : step.status === 'processing'
                          ? (isRecovering && !step.detail
                              ? '恢复中...'
                              : (step.id === 'review' && step.detail && step.detail.includes('失败项重试')
                                  ? (step.detail.includes('/')
                                      ? `失败项重试中 (${step.detail.replace(/^失败项重试\s*/, '')})`
                                      : step.detail)
                                  : (step.detail || (step.progress > 0.01 ? `${Math.round(step.progress * 100)}%` : '处理中...'))))
                          : step.status === 'error'
                            ? '失败'
                            : isRunning ? '等待中' : ''}
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-2 rounded-full transition-none ${getBarColor(step.status)}`}
                      style={
                        step.status === 'processing'
                          ? stripeFillStyle(getStepBarWidth(step), colors.stripe)
                          : { width: getStepBarWidth(step) }
                      }
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* 错误提示 */}
          {error && (
            <div className={`${compact ? 'mt-3' : 'mt-4'} p-4 bg-red-50 rounded-lg border border-red-200`}>
              <p className="text-sm text-red-800">{error}</p>
            </div>
          )}

          {/* 完成提示 -- compact 模式不显示 */}
          {!compact && canViewResult && completedMessage && onViewResult && (
            <div className="mt-8 text-center">
              <p className={`font-medium mb-4 ${isCompleted ? 'text-green-600' : 'text-amber-700'}`}>
                {isCompleted ? completedMessage : '分析已停止，但当前已有可查看的结果'}
              </p>
              <button
                onClick={onViewResult}
                className={`${colors.resultBtn} text-white px-6 py-2 rounded-md`}
              >
                {resultButtonText}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default ProgressPanel
