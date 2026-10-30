import React from 'react'
import { Link } from 'react-router-dom'
import type { Project } from '@/stores/projectStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip'
import { ClipboardCheck, ShieldAlert, PenTool, User, Calendar, RefreshCw, Trash2, ChevronRight } from 'lucide-react'
import {
  getStatusConfig,
  getTargetRoute,
  formatFullTime,
  listProgressPercent,
} from './types'
import {
  formatListAge,
  hasDistinctListUpdate,
  projectListGridTemplate,
} from './projectListRowLayout'
import './projectListRowLayout.css'

interface ProjectCardProps {
  project: Project
  isMyProject: boolean
  isAdmin: boolean
  searchQuery: string
  onDelete: (project: Project) => void
  selectionMode?: boolean
  selected?: boolean
  onToggleSelect?: (id: number) => void
}

const TypeIcon: React.FC<{ projectType: string; className?: string }> = ({ projectType, className }) => {
  if (projectType === 'bid_writing') {
    return <PenTool className={className} />
  }
  if (projectType === 'collusion_check') {
    return <ShieldAlert className={className} />
  }
  return <ClipboardCheck className={className} />
}

function highlightName(name: string, query: string): React.ReactNode {
  if (!query) return name
  const idx = name.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return name
  return (
    <>
      {name.slice(0, idx)}
      <mark className="bg-yellow-100 text-yellow-800 rounded px-0.5">
        {name.slice(idx, idx + query.length)}
      </mark>
      {name.slice(idx + query.length)}
    </>
  )
}

function ListTimeCell({ caption, at, icon }: { caption: string; at: string | null | undefined; icon?: React.ReactNode }) {
  const value = formatListAge(at)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span aria-label={`${caption} ${value}`} className="flex items-center whitespace-nowrap text-sm tabular-nums text-gray-500">
          {icon}
          {value}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>{caption}: {formatFullTime(at)}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function ListProgressCell({ status, projectType }: { status: string; projectType: string }) {
  const percent = listProgressPercent(status, projectType)
  const failed = percent === null
  return (
    <span className="flex w-full items-center gap-1.5" aria-label={failed ? '进度 失败' : `进度 ${percent}%`}>
      <span className={`h-2 min-w-0 flex-1 overflow-hidden rounded-full ${failed ? 'bg-red-100' : 'bg-gray-200'}`}>
        {!failed && (
          <span
            className={`block h-full rounded-full ${
              status === 'completed'
                ? 'bg-green-500'
                : projectType === 'bid_writing'
                  ? 'bg-teal-500'
                  : projectType === 'collusion_check'
                    ? 'bg-[#9056D8]'
                    : 'bg-blue-500'
            }`}
            style={{ width: `${percent}%` }}
          />
        )}
      </span>
      <span className={`w-8 shrink-0 text-right text-xs tabular-nums ${failed ? 'font-medium text-red-600' : 'text-gray-500'}`}>{failed ? 'fail' : `${percent}%`}</span>
    </span>
  )
}

function ListUpdateCell({ createdAt, updatedAt }: { createdAt: string; updatedAt?: string | null }) {
  if (!hasDistinctListUpdate(createdAt, updatedAt)) {
    return <span className="project-list-update-same text-sm text-gray-400" aria-label="更新时间 无单独更新">—</span>
  }
  return (
    <ListTimeCell
      caption="更新时间"
      at={updatedAt}
      icon={<RefreshCw className="project-list-mobile-only mr-1 h-3.5 w-3.5" />}
    />
  )
}

const ProjectCard: React.FC<ProjectCardProps> = ({
  project,
  isMyProject,
  isAdmin,
  searchQuery,
  onDelete,
  selectionMode = false,
  selected = false,
  onToggleSelect,
}) => {
  const statusCfg = getStatusConfig(project.status, project.projectType)
  const projectType = project.projectType || 'bid_review'
  const targetRoute = getTargetRoute(project)
  const showSelect = selectionMode && (isMyProject || isAdmin)

  return (
    <li className="group px-4 py-3.5 hover:bg-gray-50/80 transition-colors">
      <div
        className="project-list-grid"
        style={{ gridTemplateColumns: projectListGridTemplate(isAdmin, selectionMode) }}
      >
        {selectionMode && (
          showSelect ? (
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary shrink-0 cursor-pointer"
              checked={selected}
              onChange={(e) => {
                e.stopPropagation()
                onToggleSelect?.(project.id)
              }}
            />
          ) : (
            <span aria-hidden="true" />
          )
        )}

        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-primary/15 bg-primary/8 text-primary shadow-xs">
            <TypeIcon projectType={projectType} className="h-[18px] w-[18px] shrink-0" />
          </div>
          <Link to={targetRoute} className="min-w-0 flex-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <h3 className="truncate text-base font-medium text-gray-900">
                  {highlightName(project.name, searchQuery)}
                </h3>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-lg">
                <p>{project.name}</p>
              </TooltipContent>
            </Tooltip>
          </Link>
        </div>

        <div className="project-list-meta">
          {isAdmin && (
            <span
              className="flex min-w-0 items-center overflow-hidden text-sm text-gray-500"
              title={project.owner ? project.owner.username : undefined}
              aria-label={project.owner ? `创建者 ${project.owner.username}` : '创建者 未知'}
            >
              {project.owner ? (
                <>
                  <User className="project-list-mobile-only mr-1 h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{project.owner.username}</span>
                  {project.owner.id !== project.ownerId && (
                    <span className="ml-1 shrink-0 text-xs text-gray-400">(ID: {project.owner.id})</span>
                  )}
                </>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </span>
          )}
          <ListTimeCell
            caption="创建时间"
            at={project.createdAt}
            icon={<Calendar className="project-list-mobile-only mr-1 h-3.5 w-3.5" />}
          />
          <ListUpdateCell createdAt={project.createdAt} updatedAt={project.updatedAt} />
          <ListProgressCell status={project.status} projectType={projectType} />
        </div>

        <div className="project-list-actions">
          <Badge variant={statusCfg.badgeVariant} className="w-full justify-center whitespace-nowrap">
            {statusCfg.label}
          </Badge>
          <div className="flex w-full items-center justify-center gap-2">
            <Button variant="outline" size="sm" className="shrink-0 whitespace-nowrap" asChild>
              <Link to={targetRoute}>
                {statusCfg.actionLabel}
                <ChevronRight className="ml-0.5 h-3 w-3" />
              </Link>
            </Button>
            {(isMyProject || isAdmin) && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-gray-400 hover:text-red-600 hover:bg-red-50"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  onDelete(project)
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </li>
  )
}

export default ProjectCard
