import React, { useEffect, useMemo, useState } from 'react'
import type { ProcessStatusData } from '@/services/documentApi'
import { useProgressAnimation, ProgressStep } from '@/hooks/useProgressAnimation'
import ProgressPanel from '@/components/ProgressPanel'
import {
  buildProcessInitialSteps,
  mapProcessStatusForDisplay,
} from '@/components/fileProcessProgressMapper'
import { normalizeOverallStatus } from '@/services/progressStatusTransforms'

interface FileProcessProgressProps {
  processStatus: ProcessStatusData | null
  accentColor?: 'blue' | 'iris' | 'teal'
}

const FileProcessProgress: React.FC<FileProcessProgressProps> = ({
  processStatus,
  accentColor = 'blue',
}) => {
  const initialSteps = useMemo<ProgressStep[]>(() => buildProcessInitialSteps(), [])
  const { steps, displayProgress, updateTargets } = useProgressAnimation(initialSteps)
  const [stageText, setStageText] = useState('')
  const [isRunning, setIsRunning] = useState(false)

  useEffect(() => {
    if (!processStatus) return
    const mapped = mapProcessStatusForDisplay(processStatus)
    if (import.meta.env.DEV) {
      console.log('[FileProcessProgress] raw processStatus:', JSON.stringify({
        status: processStatus.status,
        stage: processStatus.stage,
        overall_status: processStatus.overall_status,
        overall_progress: processStatus.overall_progress,
        stages: processStatus.stages?.map(s => ({ key: s.key, status: s.status })),
        steps: processStatus.steps?.map(s => ({
          key: (s as unknown as Record<string, unknown>).key,
          status: s.status,
        })),
      }))
      console.log('[FileProcessProgress] mapped steps:', mapped.steps.map(s => ({ id: s.id, status: s.status, target: s.targetProgress })))
    }
    updateTargets(mapped.steps, mapped.overallTarget)
    setStageText(mapped.stageText)
    setIsRunning(normalizeOverallStatus(processStatus.status) === 'running')
  }, [processStatus, updateTargets])

  if (!processStatus) {
    return <p className="text-sm text-gray-500 mt-3">正在启动处理...</p>
  }

  if (import.meta.env.DEV) {
    console.log('[FileProcessProgress] RENDER steps passed to ProgressPanel:', steps.map(s => ({ id: s.id, status: s.status, progress: s.progress, target: s.targetProgress })))
  }

  return (
    <div className="mt-3">
      <ProgressPanel
        steps={steps}
        displayProgress={displayProgress}
        stageText={stageText}
        isRunning={isRunning}
        isCompleted={false}
        isInitialLoading={false}
        error={null}
        accentColor={accentColor}
        compact
      />
    </div>
  )
}

export default FileProcessProgress
