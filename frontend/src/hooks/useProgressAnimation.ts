import { useState, useCallback } from 'react'
import { projectProgressSnapshot } from './progressProjection'

export interface ProgressStep {
  id: string
  name: string
  status: 'pending' | 'processing' | 'completed' | 'error'
  progress: number
  targetProgress: number
  detail?: string
}

interface UseProgressAnimationReturn {
  steps: ProgressStep[]
  displayProgress: number
  updateTargets: (newSteps: ProgressStep[], overallTarget: number) => void
}

/**
 * Direct progress projection hook.
 *
 * The hook keeps the public API used by existing pages, but renders the latest
 * backend snapshot synchronously instead of easing toward it.
 */
export function useProgressAnimation(
  initialSteps: ProgressStep[],
): UseProgressAnimationReturn {
  const [steps, setSteps] = useState<ProgressStep[]>(initialSteps)
  const [displayProgress, setDisplayProgress] = useState(0)

  const updateTargets = useCallback((newSteps: ProgressStep[], overallTarget: number) => {
    const projected = projectProgressSnapshot(newSteps, overallTarget)
    setDisplayProgress(projected.displayProgress)
    setSteps(projected.steps)
  }, [])

  return { steps, displayProgress, updateTargets }
}
