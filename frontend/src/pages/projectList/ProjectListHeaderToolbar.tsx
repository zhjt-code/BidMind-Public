import React from 'react'
import { Button } from '@/components/ui/button'
import { ClipboardCheck, House, ListChecks, PenTool, Plus, ShieldAlert, X } from 'lucide-react'
import type { ProjectListContext } from './projectListDerivations'

interface ProjectListHeaderToolbarProps {
  context: ProjectListContext
  selectionMode: boolean
  onToggleSelectionMode: () => void
  onCreateProject: () => void
}

const ProjectListHeaderToolbar: React.FC<ProjectListHeaderToolbarProps> = ({
  context,
  selectionMode,
  onToggleSelectionMode,
  onCreateProject,
}) => {
  const Icon = PROJECT_LIST_CONTEXT_ICONS[context.iconName]

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <Icon className="h-5 w-5 shrink-0 text-primary" />
          <h1 className="text-xl font-semibold text-gray-900">{context.title}</h1>
          <span className="text-sm text-gray-500">{context.description}</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          className="text-gray-500 hover:text-gray-700"
          onClick={onToggleSelectionMode}
        >
          {selectionMode ? (
            <><X className="h-4 w-4" />取消选择</>
          ) : (
            <><ListChecks className="h-4 w-4" />批量管理</>
          )}
        </Button>
        <Button onClick={onCreateProject}>
          <Plus className="h-4 w-4" />
          新建项目
        </Button>
      </div>
    </div>
  )
}

const PROJECT_LIST_CONTEXT_ICONS: Record<ProjectListContext['iconName'], React.ElementType> = {
  House,
  PenTool,
  ClipboardCheck,
  ShieldAlert,
}

export default ProjectListHeaderToolbar
