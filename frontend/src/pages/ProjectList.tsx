import React, { useMemo, useRef, useState } from 'react'
import { ListChecks, X } from 'lucide-react'
import { useProjectStore, type Project } from '@/stores/projectStore'
import { useAuthStore } from '@/stores/authStore'
import { useCreateDialogStore } from '@/stores/createDialogStore'
import { useProjectDashboard } from '@/hooks/useProjectDashboard'
import { Button } from '@/components/ui/button'
import ProjectFilters from './projectList/ProjectFilters'
import ProjectListHeaderToolbar from './projectList/ProjectListHeaderToolbar'
import ProjectListContent from './projectList/ProjectListContent'
import { DeleteConfirmDialog, BatchDeleteConfirmDialog } from './projectList/CreateProjectDialog'
import ProjectDashboard from './projectList/dashboard/ProjectDashboard'
import NewUserLaunchPad from './projectList/dashboard/NewUserLaunchPad'
import ProjectDashboardFilterBar from './projectList/dashboard/ProjectDashboardFilterBar'
import type { DashboardFilterPatch } from './projectList/dashboard/projectDashboardFilters'
import { getProjectListViewState } from './projectList/projectListViewState'
import { getCreateProjectPreset, getScopedProjects } from './projectList/projectListDerivations'
import { useProjectListRefresh } from './projectList/hooks/useProjectListRefresh'
import { useProjectListView } from './projectList/hooks/useProjectListView'
import { useProjectListManagement } from './projectList/hooks/useProjectListManagement'
import BusinessHubHero from './projectList/hub/BusinessHubHero'
import BusinessHubOnboarding from './projectList/hub/BusinessHubOnboarding'
import { getBusinessHubConfig } from './projectList/hub/businessHubConfig'
import { pickRecentInProgressProject } from './projectList/hub/pickRecentInProgressProject'
import type { ProjectTypeSelection } from './projectList/types'

const ProjectListPage: React.FC = () => {
  const openCreateDialog = useCreateDialogStore((state) => state.open)
  const listAnchorRef = useRef<HTMLDivElement | null>(null)
  const [isProjectListExpanded, setProjectListExpanded] = useState(false)
  const [trendWindowDays, setTrendWindowDays] = useState(30)
  const [healthWindowDays, setHealthWindowDays] = useState(30)
  const {
    projects,
    fetchProjects,
    deleteProject,
    batchDeleteProjects,
    error,
    loading,
    hasLoadedOnce,
  } = useProjectStore()
  const { accessToken, isAuthenticated, user } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const projectList = projects as Project[]

  useProjectListRefresh({ projects: projectList, fetchProjects, isAuthenticated, accessToken })

  const view = useProjectListView({ projects: projectList, isAdmin })
  const isConsoleRoot = view.activeTab === 'all'
  const isBusinessHub = view.activeTab !== 'all'
  const hubType = isBusinessHub ? (view.activeTab as ProjectTypeSelection) : null
  const hubConfig = hubType ? getBusinessHubConfig(hubType) : null
  const scopedHubProjects = useMemo(
    () => (hubType ? getScopedProjects(projectList, hubType) : []),
    [hubType, projectList],
  )
  const recentHubProject = useMemo(
    () => pickRecentInProgressProject(scopedHubProjects),
    [scopedHubProjects],
  )
  const dashboardQuery = useProjectDashboard({
    userId: user?.id,
    role: user?.role,
    enabled: Boolean(isAuthenticated && user && isConsoleRoot),
    hasProcessingProjects: view.stats.processing > 0,
    trendWindowDays,
    healthWindowDays,
  })
  const management = useProjectListManagement({
    projects: projectList,
    pagedProjects: view.pagedProjects,
    userId: user?.id,
    isAdmin,
    deleteProject,
    batchDeleteProjects,
    selectionResetKey: view.selectionResetKey,
  })
  const viewState = getProjectListViewState({
    loading,
    hasLoadedOnce,
    error,
    projectsCount: projectList.length,
    filteredProjectsCount: view.filteredProjects.length,
  })

  const openProjectCreate = () => {
    openCreateDialog(getCreateProjectPreset(view.activeTab))
  }

  const handleDashboardFilter = (patch: DashboardFilterPatch) => {
    setProjectListExpanded(true)
    view.applyDashboardFilter(patch)
    requestAnimationFrame(() => {
      listAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const handleToggleSelectionMode = () => {
    if (!management.selectionMode) {
      setProjectListExpanded(true)
      requestAnimationFrame(() => {
        listAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    }
    management.toggleSelectionMode()
  }

  const handleHubStatClick = (filter: Parameters<typeof view.handleStatClick>[0]) => {
    view.handleStatClick(filter)
    requestAnimationFrame(() => {
      listAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  const dashboardData = dashboardQuery.data
  const shouldShowLaunchPad = isConsoleRoot && dashboardData?.summary.total === 0
  const shouldShowProjectList = !isConsoleRoot || isProjectListExpanded
  const hubHasProjects = scopedHubProjects.length > 0
  const hubReady = hasLoadedOnce && !loading
  const showHubOnboarding = Boolean(isBusinessHub && hubConfig && hubReady && !hubHasProjects)
  const showHubHero = Boolean(isBusinessHub && hubConfig && hubHasProjects)
  const showHubListSection = Boolean(isConsoleRoot || hubHasProjects || (isBusinessHub && !hubReady))

  return (
    <div className="space-y-3 -mt-3">
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      {isConsoleRoot && (
        <ProjectListHeaderToolbar
          context={view.context}
          selectionMode={management.selectionMode}
          onToggleSelectionMode={handleToggleSelectionMode}
          onCreateProject={openProjectCreate}
        />
      )}

      {isBusinessHub && !hubReady && (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="h-7 w-40 animate-pulse rounded bg-slate-200" />
          <div className="mt-3 h-4 w-full max-w-xl animate-pulse rounded bg-slate-100" />
          <div className="mt-5 grid gap-2 sm:grid-cols-3">
            <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
            <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
            <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
          </div>
        </div>
      )}

      {showHubHero && hubConfig && (
        <BusinessHubHero
          config={hubConfig}
          stats={view.stats}
          recentProject={recentHubProject}
          onCreateProject={openProjectCreate}
          onStatClick={handleHubStatClick}
        />
      )}

      {showHubOnboarding && hubConfig && (
        <BusinessHubOnboarding
          config={hubConfig}
          onCreateProject={openProjectCreate}
        />
      )}

      {isConsoleRoot && dashboardQuery.isLoading && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow">
          <div className="h-6 w-48 animate-pulse rounded bg-slate-200" />
          <div className="mt-4 grid gap-3 lg:grid-cols-3">
            <div className="h-28 animate-pulse rounded-xl bg-slate-100" />
            <div className="h-28 animate-pulse rounded-xl bg-slate-100" />
            <div className="h-28 animate-pulse rounded-xl bg-slate-100" />
          </div>
        </div>
      )}

      {isConsoleRoot && dashboardQuery.isError && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          控制台数据加载失败，您仍可使用下方项目列表。
        </div>
      )}

      {shouldShowLaunchPad ? (
        <NewUserLaunchPad />
      ) : (
        <>
          {isConsoleRoot && dashboardData && (
            <ProjectDashboard
              data={dashboardData}
              isFetching={dashboardQuery.isFetching}
              trendWindowDays={trendWindowDays}
              onTrendWindowDaysChange={setTrendWindowDays}
              healthWindowDays={healthWindowDays}
              onHealthWindowDaysChange={setHealthWindowDays}
              onRefresh={() => void dashboardQuery.refetch()}
              onApplyFilter={handleDashboardFilter}
            />
          )}

          {showHubListSection && (
            <section ref={listAnchorRef} className="scroll-mt-4 rounded-lg border border-slate-200 bg-white shadow-sm">
              {isConsoleRoot && (
                <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">项目列表</h2>
                    <p className="text-xs text-slate-500">当前显示 {view.filteredProjects.length} 个项目</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <ProjectDashboardFilterBar
                      filter={view.dashboardFilter}
                      onClear={() => handleDashboardFilter({ clear: true })}
                    />
                    {management.selectionMode && (
                      <button
                        type="button"
                        onClick={handleToggleSelectionMode}
                        className="inline-flex h-8 items-center justify-center rounded-md border border-slate-200 px-3 text-sm text-slate-600 transition hover:bg-slate-50"
                      >
                        取消选择
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setProjectListExpanded((value) => !value)}
                      className="inline-flex h-8 items-center justify-center rounded-md border border-slate-200 px-3 text-sm text-slate-600 transition hover:bg-slate-50"
                    >
                      {isProjectListExpanded ? '收起列表' : '展开列表'}
                    </button>
                  </div>
                </div>
              )}

              {isBusinessHub && hubHasProjects && (
                <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <h2 className="text-sm font-semibold text-slate-900">
                    项目列表
                    <span className="ml-1.5 font-normal text-slate-500">({view.filteredProjects.length})</span>
                  </h2>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 text-slate-600 hover:text-slate-800"
                    onClick={handleToggleSelectionMode}
                  >
                    {management.selectionMode ? (
                      <><X className="h-4 w-4" />取消选择</>
                    ) : (
                      <><ListChecks className="h-4 w-4" />批量管理</>
                    )}
                  </Button>
                </div>
              )}

              {shouldShowProjectList && (
                <div className={isConsoleRoot || isBusinessHub ? 'border-t border-slate-100 p-3' : ''}>
                  <ProjectFilters
                    searchInput={view.searchInput}
                    onSearchChange={view.setSearchInput}
                    sortKey={view.sortKey}
                    onSortChange={view.handleSortChange}
                    statusFilter={view.statusFilter}
                    onStatusFilterChange={view.handleStatusFilterChange}
                    updatedWithin={view.updatedWithin}
                    onUpdatedWithinChange={view.handleUpdatedWithinChange}
                    isAdmin={isAdmin}
                    usernameSearch={view.usernameSearchInput}
                    onUsernameSearchChange={view.setUsernameSearchInput}
                  />

                  <ProjectListContent
                    viewState={viewState}
                    error={error}
                    activeTab={view.activeTab}
                    searchQuery={view.searchInput.trim()}
                    filteredCount={view.filteredProjects.length}
                    currentPage={view.currentPage}
                    pagedProjects={view.pagedProjects}
                    selectableProjects={management.selectableProjects}
                    selectedIds={management.selectedIds}
                    selectionMode={management.selectionMode}
                    userId={user?.id}
                    isAdmin={isAdmin}
                    onReload={() => void fetchProjects()}
                    onClearSearch={() => view.setSearchInput('')}
                    onCreateProject={openProjectCreate}
                    onToggleSelect={management.toggleSelect}
                    onToggleSelectAll={management.toggleSelectAll}
                    onOpenBatchDeleteConfirm={management.openBatchDeleteConfirm}
                    onDeleteProject={management.setDeleteConfirm}
                    onPageChange={view.handlePageChange}
                  />
                </div>
              )}
            </section>
          )}
        </>
      )}

      <DeleteConfirmDialog
        project={management.deleteConfirm}
        loading={management.deleting}
        onCancel={() => { if (!management.deleting) management.setDeleteConfirm(null) }}
        onConfirm={management.handleDeleteProject}
      />
      <BatchDeleteConfirmDialog
        open={management.batchDeleteConfirm}
        selectedProjects={management.batchDeleteTargets}
        loading={management.batchDeleting}
        onCancel={() => { if (!management.batchDeleting) management.setBatchDeleteConfirm(false) }}
        onConfirm={management.handleBatchDelete}
      />
    </div>
  )
}

export default ProjectListPage
