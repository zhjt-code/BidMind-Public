import { api } from './httpClient'
import type { ApiResponse, PaginatedResponse } from './apiTypes'
import { normalizeRunMode } from '@/lib/runMode'
import type { Project } from '@/stores/projectStore'

interface OwnerInfo {
  id: number
  username: string
}

interface ProjectResponse {
  id: number
  name: string
  description?: string
  status: string
  project_type?: string
  owner_id?: number
  owner?: OwnerInfo
  error_message?: string | null
  run_mode?: string
  created_at: string
  updated_at: string
}

export function projectQueryKey(projectId: number) {
  return ['project', projectId] as const
}

const transformProject = (project: ProjectResponse): Project => ({
  id: project.id,
  name: project.name,
  description: project.description,
  status: project.status,
  projectType: project.project_type || 'bid_review',
  ownerId: project.owner_id,
  owner: project.owner ? {
    id: project.owner.id,
    username: project.owner.username,
  } : null,
  errorMessage: project.error_message ?? null,
  runMode: normalizeRunMode(project.run_mode),
  createdAt: project.created_at,
  updatedAt: project.updated_at,
})

export const projectService = {
  getProjects: async (pageSize = 500) => {
    const allItems: ProjectResponse[] = []
    let page = 1
    let totalPages = 1

    // 项目列表页统计依赖完整数据集，不能只取第一页。
    do {
      const response = await api.get<PaginatedResponse<ProjectResponse>>(`/projects?page=${page}&page_size=${pageSize}`)
      const items = response.items || []
      allItems.push(...items)

      totalPages = response.total_pages || Math.max(1, Math.ceil((response.total || items.length) / pageSize))
      page += 1

      if (items.length === 0) {
        break
      }
    } while (page <= totalPages)

    return allItems.map(transformProject)
  },

  getProject: async (id: number) => {
    const response = await api.get<ApiResponse<ProjectResponse>>(`/projects/${id}`)
    return transformProject(response.data)
  },

  setRunMode: async (id: number, runMode: 'guided' | 'express') => {
    const response = await api.patch<ApiResponse<ProjectResponse>>(`/projects/${id}/run-mode`, {
      run_mode: runMode,
    })
    return transformProject(response.data)
  },

  createProject: async (name: string, projectType: string = 'bid_review') => {
    const response = await api.post<ApiResponse<ProjectResponse>>('/projects', {
      name,
      project_type: projectType,
    })
    return transformProject(response.data)
  },

  deleteProject: async (id: number) => {
    return api.delete(`/projects/${id}`, { timeout: 60000 })
  },

  batchDeleteProjects: async (ids: number[]): Promise<{ deleted: number[]; failed: { id: number; reason: string }[] }> => {
    const response = await api.post<ApiResponse<{ deleted: number[]; failed: { id: number; reason: string }[] }>>(
      '/projects/batch-delete',
      { project_ids: ids },
    )
    return response.data
  },
}
