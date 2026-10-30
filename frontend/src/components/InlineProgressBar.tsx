import React, { useMemo } from 'react'
import { Loader2, CheckCircle2 } from 'lucide-react'
import { useProgressAnimation, type ProgressStep } from '@/hooks/useProgressAnimation'

interface InlineProgressBarProps {
  title: string
  detail?: string
  progress: number
  accentColor?: 'blue' | 'teal' | 'iris'
  completed?: boolean
  indeterminate?: boolean
}

const COLOR_MAP = {
  blue: { barDone: 'bg-green-500', spinner: 'text-blue-500', stripe: '#3b82f6' },
  teal: { barDone: 'bg-green-500', spinner: 'text-teal-500', stripe: '#14b8a6' },
  iris: { barDone: 'bg-green-500', spinner: 'text-[#9056D8]', stripe: '#9056D8' },
} as const

const PLACEHOLDER_STEPS: ProgressStep[] = [
  { id: '_inline', name: '', status: 'processing', progress: 0, targetProgress: 0 },
]

const InlineProgressBar: React.FC<InlineProgressBarProps> = ({
  title,
  detail,
  progress,
  accentColor = 'teal',
  completed = false,
  indeterminate = false,
}) => {
  const colors = COLOR_MAP[accentColor]
  const safeProgress = Math.max(0, Math.min(1, progress))

  const initialSteps = useMemo(() => PLACEHOLDER_STEPS, [])
  const { displayProgress, updateTargets } = useProgressAnimation(initialSteps)

  const prevTargetRef = React.useRef(0)
  React.useEffect(() => {
    const target = completed ? 1 : safeProgress
    if (target !== prevTargetRef.current) {
      prevTargetRef.current = target
      const step: ProgressStep = {
        id: '_inline',
        name: '',
        status: completed ? 'completed' : 'processing',
        progress: 0,
        targetProgress: target,
      }
      updateTargets([step], target)
    }
  }, [safeProgress, completed, updateTargets])

  const visualPct = completed ? 1 : displayProgress

  const pctText = `${Math.round(Math.min(displayProgress, 1) * 100)}%`

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          {completed ? (
            <CheckCircle2 className="h-5 w-5 text-green-500 shrink-0" />
          ) : (
            <Loader2 className={`h-5 w-5 animate-spin shrink-0 ${colors.spinner}`} />
          )}
          <span className="text-sm font-medium text-gray-700">{title}</span>
        </div>
        <span className="text-sm text-gray-500 truncate ml-4 max-w-[50%] text-right">
          {detail || pctText}
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2 overflow-hidden">
        {indeterminate && !completed ? (
          <div
            className="h-2 rounded-full progress-stripe animate-indeterminate"
            style={{ width: '30%', ['--progress-stripe-color']: colors.stripe } as React.CSSProperties}
          />
        ) : (
          <div
            className={`h-2 rounded-full transition-none ${completed ? colors.barDone : 'progress-stripe'}`}
            style={
              completed
                ? { width: `${visualPct * 100}%` }
                : ({
                    width: `${visualPct * 100}%`,
                    ['--progress-stripe-color']: colors.stripe,
                  } as React.CSSProperties)
            }
          />
        )}
      </div>
    </div>
  )
}

export default InlineProgressBar
