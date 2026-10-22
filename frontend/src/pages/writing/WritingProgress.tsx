/**
 * 职责: 写标一步到位的跨阶段进度页
 * 不做: 不编辑约束/目录/绑定，不上传素材
 */
import React from 'react'
import { useParams } from 'react-router-dom'
import GenericProgressPage, { type CommonStatus, type ProgressPageConfig } from '@/components/GenericProgressPage'
import { writingService } from '@/services/writingApi'
import { useWritingGuard } from './useWritingGuard'
import WritingStepBar from './WritingStepBar'
import { mapWritingExpressStatus, WRITING_EXPRESS_STEPS } from './writingExpressProgress'

async function fetchWritingExpressStatus(projectId: number): Promise<CommonStatus> {
  const status = await writingService.getStatus(projectId)
  return mapWritingExpressStatus(status.status, status)
}

const CONFIG: ProgressPageConfig = {
  title: '标书编写进度',
  initialSteps: WRITING_EXPRESS_STEPS,
  queryKey: 'writing-express-progress',
  fetchStatus: fetchWritingExpressStatus,
  accentColor: 'teal',
  backPath: (pid) => `/project/${pid}/writing/upload`,
  nextPath: (pid) => `/project/${pid}/writing/preview`,
  completedMessage: '正文生成完成!',
  resultButtonText: '查看预览导出',
  description: '一步到位：约束、目录、绑定按默认连续推进。',
  textMap: { completed: '编写完成', failed: '编写失败', running: '编写中...' },
  autoNavigateOnCompleteMs: 1500,
  balanceGateNodeKey: 'writing_compose',
  retryAction: (projectId, nodeKey, targetId) =>
    writingService.retryWritingNode(projectId, nodeKey || 'writing_extract', targetId ?? undefined),
}

const WritingProgressPage: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const { projectStatus, loading } = useWritingGuard(pid, 'extracted')

  if (loading) {
    return <div className="py-20 text-center text-sm text-muted-foreground">加载编写进度...</div>
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={1} projectStatus={projectStatus} />
      <GenericProgressPage config={CONFIG} />
    </div>
  )
}

export default WritingProgressPage
