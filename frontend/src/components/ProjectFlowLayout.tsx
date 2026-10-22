import React from 'react'
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { projectQueryKey, projectService } from '@/services/projectApi'
import { useProjectStore } from '@/stores/projectStore'
import ProjectContextBar from './ProjectContextBar'
import { getFlowTypeFromPath, getProjectListReturnPath } from './projectFlowContext'

const ProjectFlowLayout: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const projectId = Number(id)
  const hasValidProjectId = Number.isFinite(projectId)

  const cachedProject = useProjectStore((state) => (
    hasValidProjectId ? state.projects.find((project) => project.id === projectId) : undefined
  ))

  const { data: project, isLoading } = useQuery({
    queryKey: projectQueryKey(projectId),
    queryFn: () => projectService.getProject(projectId),
    enabled: hasValidProjectId,
    initialData: cachedProject,
  })

  const fallbackFlowType = getFlowTypeFromPath(location.pathname)
  const returnPath = getProjectListReturnPath(project?.projectType ?? fallbackFlowType)

  return (
    <div className="-mt-8 flex min-h-0 flex-1 flex-col">
      <ProjectContextBar
        projectId={hasValidProjectId ? projectId : undefined}
        projectName={project?.name}
        loading={isLoading}
        onBackToList={() => navigate(returnPath)}
      />
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
    </div>
  )
}

export default ProjectFlowLayout
