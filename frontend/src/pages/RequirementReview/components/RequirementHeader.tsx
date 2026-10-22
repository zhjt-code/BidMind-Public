/**
 * 招标要求确认页顶栏。
 *
 * 包含: 返回按钮 + 标题 + 知识库仪器槽 + 添加按钮 + 重新提取入口 + 主流程按钮。
 * 主流程按钮由服务端项目状态派生：未发起时确认启动，已发起后只允许进入进度/结果。
 *
 * 文档未就绪时确认按钮 disabled,title 显示对应文案 (tender / bid 任一未就绪)。
 */
import React from 'react'
import { ArrowLeft, ArrowRight, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { RequirementReviewPrimaryAction } from '../reviewFlowState'

export interface RequirementHeaderProps {
  primaryAction: RequirementReviewPrimaryAction
  requirementsCount: number
  confirming: boolean
  reextracting: boolean
  tenderDocReady: boolean | null
  bidDocReady: boolean | null
  tenderDocMessage: string
  bidDocMessage: string
  onBack: () => void
  onOpenAddDialog: () => void
  onOpenReextractDialog: () => void
  onGoToPrimaryAction: () => void
  onConfirmAll: () => void
  knowledgeSlot?: React.ReactNode
}

export const RequirementHeader: React.FC<RequirementHeaderProps> = ({
  primaryAction,
  requirementsCount,
  confirming,
  reextracting,
  tenderDocReady,
  bidDocReady,
  tenderDocMessage,
  bidDocMessage,
  onBack,
  onOpenAddDialog,
  onOpenReextractDialog,
  onGoToPrimaryAction,
  onConfirmAll,
  knowledgeSlot,
}) => {
  const confirmDisabled =
    confirming || requirementsCount === 0 || !tenderDocReady || !bidDocReady
  const confirmTitle = !tenderDocReady
    ? tenderDocMessage
    : !bidDocReady
      ? bidDocMessage
      : ''

  return (
    <div className="flex justify-between items-start mb-6">
      <div className="flex items-start">
        <Button
          variant="ghost"
          size="icon"
          onClick={onBack}
          className="mr-4"
          title="返回文件上传"
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">招标要求确认</h1>
          <p className="text-gray-500 mt-1">确认后开始响应提取与对比审查。否决项不符合即不通过。</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {knowledgeSlot}
        <Button variant="outline" size="sm" onClick={onOpenAddDialog}>
          <Plus className="h-4 w-4 mr-1" />
          添加
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenReextractDialog}
          disabled={reextracting || !tenderDocReady || !bidDocReady}
          title="从已解析文档重新提取招标要求"
        >
          <RefreshCw className={`h-4 w-4 mr-1 ${reextracting ? 'animate-spin' : ''}`} />
          重新提取
        </Button>
        {primaryAction.kind === 'confirm' ? (
          <Button onClick={onConfirmAll} disabled={confirmDisabled} title={confirmTitle}>
            {confirming ? '处理中...' : primaryAction.label}
          </Button>
        ) : (
          <Button
            variant="default"
            className="bg-green-500 hover:bg-green-600"
            onClick={onGoToPrimaryAction}
            title={primaryAction.title}
          >
            {primaryAction.label}
            <ArrowRight className="h-4 w-4 ml-1" />
          </Button>
        )}
      </div>
    </div>
  )
}
