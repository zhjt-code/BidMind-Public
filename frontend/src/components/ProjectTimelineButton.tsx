import React from 'react'
import { useNavigate } from 'react-router-dom'
import { History } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores'

interface ProjectTimelineButtonProps {
  projectId: number | string | null | undefined
  className?: string
  label?: string
}

/**
 * 项目级系统日志跳转按钮 (仅管理员可见)。
 *
 * 跳转到 /admin/projects/:projectId/timeline, 展示该项目的融合日志时间线
 * (错误日志 + 任务观测 + 里程碑 + 审计)。
 *
 * 用于三大业务 Result 页头部, 让管理员在查看业务结果时能直接看到
 * 该项目的全程系统日志, 辅助排查处理过程中的异常。
 */
const ProjectTimelineButton: React.FC<ProjectTimelineButtonProps> = ({
  projectId,
  className,
  label = '系统日志',
}) => {
  const navigate = useNavigate()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  if (!isAdmin || projectId == null || projectId === '') return null
  const id = typeof projectId === 'string' ? Number(projectId) : projectId
  if (!Number.isInteger(id) || id <= 0) return null

  return (
    <Button
      variant="outline"
      size="sm"
      className={className}
      onClick={() => navigate(`/admin/projects/${id}/timeline`)}
      title="查看该项目的全部系统日志（仅管理员可见）"
    >
      <History className="h-4 w-4 mr-1" />
      {label}
    </Button>
  )
}

export default ProjectTimelineButton
