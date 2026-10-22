import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { projectQueryKey, projectService } from '@/services/projectApi'
import { getWritingProjectRoute, isWritingExpressPipeline } from './writingConstants'

const STEPS = [
  { label: '上传文件', path: 'upload', minStatus: 0 },
  { label: '确认约束', path: 'clauses', minStatus: 4 },
  { label: '目录与素材', path: 'outline', minStatus: 4 },
  { label: '内容编辑', path: 'compose', minStatus: 4 },
  { label: '预览导出', path: 'preview', minStatus: 4 },
]

const STATUS_TO_STEP: Record<string, number> = {
  created: 0,
  uploading: 0,
  parsing: 0,
  parsed: 0,
  extracting: 0,
  extracted: 1,
  outlining: 2,
  outlined: 2,
  matching: 2,
  matched: 3,
  writing: 3,
  composed: 4,
  formatting: 4,
  completed: 5,
  failed: 0,
}

interface WritingStepBarProps {
  projectId: number
  currentStep: number
  projectStatus?: string | null
}

const WritingStepBar: React.FC<WritingStepBarProps> = ({
  projectId,
  currentStep,
  projectStatus,
}) => {
  const navigate = useNavigate()
  const completedStep = STATUS_TO_STEP[projectStatus || ''] ?? 0
  const { data: project } = useQuery({
    queryKey: projectQueryKey(projectId),
    queryFn: () => projectService.getProject(projectId),
  })
  const runMode = project?.runMode ?? 'guided'

  return (
    <div className="flex items-center gap-1 mb-6 bg-white rounded-lg border px-4 py-3">
      {STEPS.map((step, idx) => {
        const isActive = idx === currentStep
        const isDone = idx < completedStep
        const isClickable = isDone || idx === completedStep

        return (
          <React.Fragment key={step.path}>
            {idx > 0 && (
              <div className={`flex-1 h-0.5 mx-1 ${
                idx <= completedStep ? 'bg-teal-500' : 'bg-gray-200'
              }`} />
            )}
            <button
              onClick={() => {
                if (isClickable) {
                  if (
                    isWritingExpressPipeline(projectStatus || '', runMode)
                    && step.path !== 'upload'
                    && step.path !== 'preview'
                  ) {
                    navigate(getWritingProjectRoute(projectId, projectStatus || 'extracted', runMode))
                    return
                  }
                  const url = `/project/${projectId}/writing/${step.path}`
                  navigate(step.path === 'upload' && idx !== currentStep ? `${url}?from=stepbar` : url)
                }
              }}
              disabled={!isClickable}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition whitespace-nowrap ${
                isActive
                  ? 'bg-teal-500 text-white'
                  : isDone
                  ? 'bg-teal-50 text-teal-700 cursor-pointer hover:bg-teal-100'
                  : isClickable
                  ? 'bg-gray-100 text-gray-700 cursor-pointer hover:bg-gray-200'
                  : 'bg-gray-50 text-gray-400 cursor-default'
              }`}
            >
              {isDone ? (
                <Check className="h-3 w-3" />
              ) : (
                <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${
                  isActive ? 'bg-white/20' : 'bg-gray-200'
                }`}>
                  {idx + 1}
                </span>
              )}
              {step.label}
            </button>
          </React.Fragment>
        )
      })}
    </div>
  )
}

export default WritingStepBar
