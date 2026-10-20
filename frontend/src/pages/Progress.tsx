import React from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { reviewService } from '@/services/reviewApi'
import { projectQueryKey, projectService } from '@/services/projectApi'
import { ProgressStep } from '@/hooks/useProgressAnimation'
import GenericProgressPage, { ProgressPageConfig } from '@/components/GenericProgressPage'
import { EXPRESS_REVIEW_STEPS } from './reviewProgress/expressReviewStatus'
import {
  fetchExpressReviewStatus,
  useExpressReviewExtract,
} from './reviewProgress/useExpressReviewExtract'

const INITIAL_STEPS: ProgressStep[] = [
  { id: 'extract', name: '投标响应提取', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'match',   name: '响应装配校验', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'review',  name: 'AI对比审查',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'report',  name: '结果准备', status: 'pending', progress: 0, targetProgress: 0 },
]

const CONFIG: ProgressPageConfig = {
  title: 'AI审查进度',
  initialSteps: INITIAL_STEPS,
  queryKey: 'review-progress',
  fetchStatus: (projectId) => reviewService.getReviewStatus(projectId),
  accentColor: 'blue',
  backPath: (pid) => `/project/${pid}/requirements`,
  nextPath: (pid) => `/project/${pid}/result`,
  completedMessage: '审查完成!',
  resultButtonText: '查看审查结果',
  description: '完成后进入审查结果。',
  textMap: { completed: '审查完成', failed: '审查失败', running: '审查中...' },
  forceAllCompleted: true,
  autoNavigateOnCompleteMs: 1500,
  balanceGateNodeKey: 'review_retry',
  retryAction: (projectId, nodeKey, targetId) => reviewService.retryReview(projectId, nodeKey, targetId),
}

const EXPRESS_CONFIG: ProgressPageConfig = {
  ...CONFIG,
  initialSteps: EXPRESS_REVIEW_STEPS,
  queryKey: 'review-express-progress',
  fetchStatus: fetchExpressReviewStatus,
  backPath: (pid) => `/project/${pid}/upload`,
  description: '一步到位：按默认确认提取项后连续审查。',
}

const ProgressPage: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const { data: project, isLoading } = useQuery({
    queryKey: projectQueryKey(projectId),
    queryFn: () => projectService.getProject(projectId),
    enabled: Number.isFinite(projectId),
  })
  useExpressReviewExtract(projectId, project?.runMode, project?.status)

  if (isLoading || !project) {
    return (
      <div className="py-20 text-center text-sm text-muted-foreground">加载审查进度...</div>
    )
  }

  return (
    <GenericProgressPage
      config={project.runMode === 'express' ? EXPRESS_CONFIG : CONFIG}
    />
  )
}

export default ProgressPage
