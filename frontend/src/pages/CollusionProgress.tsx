import React from 'react'
import { collusionService } from '@/services/collusionApi'
import { ProgressStep } from '@/hooks/useProgressAnimation'
import GenericProgressPage, { ProgressPageConfig } from '@/components/GenericProgressPage'

const INITIAL_STEPS: ProgressStep[] = [
  { id: 'extract',    name: '文档信息提取',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'global',     name: '全局特征分析',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'compare',    name: '重点文件对比',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'cluster',    name: '嫌疑群组识别',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'synthesize', name: '群组深度研判',   status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'report',     name: '生成审查报告',   status: 'pending', progress: 0, targetProgress: 0 },
]

const CONFIG: ProgressPageConfig = {
  title: '串标分析进度',
  initialSteps: INITIAL_STEPS,
  queryKey: 'collusion-progress',
  fetchStatus: (projectId) => collusionService.getStatus(projectId),
  accentColor: 'iris',
  backPath: () => '/',
  nextPath: (pid) => `/project/${pid}/collusion/result`,
  completedMessage: '串标分析完成!',
  resultButtonText: '查看分析结果',
  description: '深度分析通常需要 5–15 分钟，完成后进入分析结果。',
  textMap: { completed: '分析完成', failed: '分析失败', running: '分析中...' },
  forceAllCompleted: false,
  autoNavigateOnCompleteMs: 1500,
  balanceGateNodeKey: 'collusion_retry',
  retryAction: (projectId, nodeKey, targetId) => collusionService.retryAnalysis(projectId, nodeKey, targetId),
}

const CollusionProgressPage: React.FC = () => <GenericProgressPage config={CONFIG} />

export default CollusionProgressPage
