import React from 'react'
import { Button } from '@/components/ui/button'
import { EmptyState, SkeletonList } from '@/components'
import { Trash2 } from 'lucide-react'
import type { Project } from '@/stores/projectStore'
import ProjectCard from './ProjectCard'
import Pagination from './Pagination'
import { PAGE_SIZE, type TabKey } from './types'
import type { ProjectListViewState } from './projectListViewState'
import { projectListColumnAlignClass, projectListColumnLabels, projectListGridTemplate } from './projectListRowLayout'
import './projectListRowLayout.css'

const INITIAL_EMPTY_DESCRIPTIONS: Record<TabKey, string> = {
  all: '点击上方「新建项目」开始您的第一个项目',
  bid_writing: '点击上方「新建项目」开始您的第一个标书编写项目',
  bid_review: '点击上方「新建项目」开始您的第一个投标审查项目',
  collusion_check: '点击上方「新建项目」开始您的第一个串标审查项目',
}

interface ProjectListContentProps {
  viewState: ProjectListViewState
  error: string | null
  activeTab: TabKey
  searchQuery: string
  filteredCount: number
  currentPage: number
  pagedProjects: Project[]
  selectableProjects: Project[]
  selectedIds: Set<number>
  selectionMode: boolean
  userId?: number
  isAdmin: boolean
  onReload: () => void
  onClearSearch: () => void
  onCreateProject: () => void
  onToggleSelect: (id: number) => void
  onToggleSelectAll: () => void
  onOpenBatchDeleteConfirm: () => void
  onDeleteProject: (project: Project) => void
  onPageChange: (page: number) => void
}

const ProjectListContent: React.FC<ProjectListContentProps> = ({
  viewState,
  error,
  activeTab,
  searchQuery,
  filteredCount,
  currentPage,
  pagedProjects,
  selectableProjects,
  selectedIds,
  selectionMode,
  userId,
  isAdmin,
  onReload,
  onClearSearch,
  onCreateProject,
  onToggleSelect,
  onToggleSelectAll,
  onOpenBatchDeleteConfirm,
  onDeleteProject,
  onPageChange,
}) => (
  <div className="project-list-shell bg-white rounded-lg shadow-sm">
    {viewState === 'loading' ? (
      <SkeletonList rows={5} />
    ) : viewState === 'load_error' ? (
      <EmptyState
        variant="folder"
        title="项目列表加载失败"
        description={error || '请稍后重试'}
        action={{ label: '重新加载', onClick: onReload }}
      />
    ) : viewState === 'empty_all' ? (
      <EmptyState
        variant="folder"
        title="暂无项目"
        description={INITIAL_EMPTY_DESCRIPTIONS[activeTab]}
        descriptionClassName="max-w-full sm:max-w-none sm:whitespace-nowrap"
      />
    ) : viewState === 'empty_filtered' ? (
      <FilteredEmptyState
        activeTab={activeTab}
        searchQuery={searchQuery}
        onClearSearch={onClearSearch}
        onCreateProject={onCreateProject}
      />
    ) : (
      <>
        {selectionMode && selectableProjects.length > 0 && (
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                checked={selectableProjects.length > 0 && selectableProjects.every((project) => selectedIds.has(project.id))}
                onChange={onToggleSelectAll}
              />
              全选当前页 ({selectedIds.size}/{selectableProjects.length})
            </label>

            {selectedIds.size > 0 && (
              <Button
                variant="destructive"
                size="sm"
                onClick={onOpenBatchDeleteConfirm}
              >
                <Trash2 className="h-4 w-4 mr-1" />
                删除选中 ({selectedIds.size})
              </Button>
            )}
          </div>
        )}
        <ProjectListColumnHeader isAdmin={isAdmin} selectionMode={selectionMode} />
        <ul className="divide-y divide-gray-200">
          {pagedProjects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              isMyProject={project.ownerId === userId}
              isAdmin={!!isAdmin}
              searchQuery={searchQuery}
              onDelete={onDeleteProject}
              selectionMode={selectionMode}
              selected={selectedIds.has(project.id)}
              onToggleSelect={onToggleSelect}
            />
          ))}
        </ul>
        <Pagination
          total={filteredCount}
          pageSize={PAGE_SIZE}
          currentPage={currentPage}
          onChange={onPageChange}
        />
      </>
    )}
  </div>
)

interface FilteredEmptyStateProps {
  activeTab: TabKey
  searchQuery: string
  onClearSearch: () => void
  onCreateProject: () => void
}

const FilteredEmptyState: React.FC<FilteredEmptyStateProps> = ({
  activeTab,
  searchQuery,
  onClearSearch,
  onCreateProject,
}) => {
  const projectTypeLabel = getProjectTypeLabel(activeTab)
  if (searchQuery) {
    return (
      <EmptyState
        variant="search"
        title={`未找到与"${searchQuery}"相关的${projectTypeLabel}项目`}
        description="请尝试修改关键词或清空搜索框查看更多项目"
        action={{ label: '清空搜索', onClick: onClearSearch }}
      />
    )
  }

  return (
    <EmptyState
      variant="folder"
      title={activeTab === 'all' ? '没有匹配的项目' : `暂无${projectTypeLabel}项目`}
      description={activeTab === 'all' ? '请调整筛选条件或新建项目' : `点击上方「新建项目」创建${projectTypeLabel}项目`}
      action={{ label: '新建项目', onClick: onCreateProject }}
    />
  )
}

function ProjectListColumnHeader({ isAdmin, selectionMode }: { isAdmin: boolean; selectionMode: boolean }) {
  return (
    <div
      className="project-list-head border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500"
      style={{ gridTemplateColumns: projectListGridTemplate(isAdmin, selectionMode) }}
    >
      {projectListColumnLabels(isAdmin, selectionMode).map((column) => (
        <span key={column.key} className={projectListColumnAlignClass(column.align)}>
          {column.key === 'name' ? (
            <span className="flex items-center gap-3 whitespace-nowrap">
              <span className="w-10 shrink-0" aria-hidden="true" />
              {column.label}
            </span>
          ) : column.label}
        </span>
      ))}
    </div>
  )
}

function getProjectTypeLabel(activeTab: TabKey): string {
  if (activeTab === 'bid_writing') return '标书编写'
  if (activeTab === 'bid_review') return '投标审查'
  if (activeTab === 'collusion_check') return '串标审查'
  return ''
}

export default ProjectListContent
