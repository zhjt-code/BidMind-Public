import { create } from 'zustand'
import { projectService } from '@/services/projectApi'
import type { RunMode } from '@/lib/runMode'
import { useAuthStore } from './authStore'
import { shouldResetProjectStateForAuthChange } from './projectStoreState'

export interface ProjectOwner {
  id: number
  username: string
}

export interface Project {
  id: number
  name: string
  description?: string
  status: string
  projectType?: string
  runMode?: RunMode
  errorMessage?: string | null
  createdAt: string
  updatedAt: string
  ownerId?: number
  owner?: ProjectOwner | null
}

interface BatchDeleteResult {
  deleted: number[]
  failed: { id: number; reason: string }[]
}

interface ProjectStore {
  projects: Project[]
  currentProject: Project | null
  loading: boolean
  hasLoadedOnce: boolean
  loadedForUserId: number | null
  error: string | null
  setCurrentProject: (project: Project | null) => void
  fetchProjects: () => Promise<void>
  createProject: (name: string, projectType?: string) => Promise<Project>
  deleteProject: (id: number) => Promise<void>
  batchDeleteProjects: (ids: number[]) => Promise<BatchDeleteResult>
}

export const useProjectStore = create<ProjectStore>((set, _get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  hasLoadedOnce: false,
  loadedForUserId: null,
  error: null,

  setCurrentProject: (project) => set({ currentProject: project }),

  fetchProjects: async () => {
    const { hasLoadedOnce, projects } = _get()
    const currentUserId = useAuthStore.getState().user?.id ?? null
    const shouldShowLoading = !hasLoadedOnce || projects.length === 0
    set({ loading: shouldShowLoading, error: null })
    try {
      const data = await projectService.getProjects()
      set({
        projects: data,
        loading: false,
        hasLoadedOnce: true,
        loadedForUserId: currentUserId,
      })
    } catch (error) {
      let errorMessage = '加载项目失败'
      if (error instanceof Error) {
        const errMsg = error.message.toLowerCase()
        if (errMsg.includes('401') || errMsg.includes('认证') || errMsg.includes('unauthorized')) {
          errorMessage = '登录已过期，请重新登录'
        } else if (errMsg.includes('403') || errMsg.includes('权限')) {
          errorMessage = '无权限访问'
        } else if (errMsg.includes('请联系管理员')) {
          errorMessage = '服务暂时不可用，请稍后重试'
        } else {
          errorMessage = error.message || '加载项目失败'
        }
      }
      set({
        error: errorMessage,
        loading: false,
        hasLoadedOnce: true,
        loadedForUserId: currentUserId,
      })
    }
  },

  createProject: async (name: string, projectType: string = 'bid_review') => {
    set({ loading: true, error: null })
    try {
      const newProject = await projectService.createProject(name, projectType)
      // 新项目添加到列表最前面
      set((state) => ({ 
        projects: [newProject, ...state.projects], 
        loading: false 
      }))
      return newProject
    } catch (error) {
      set({ 
        error: error instanceof Error ? error.message : '创建项目失败', 
        loading: false 
      })
      throw error
    }
  },

  deleteProject: async (id: number) => {
    // 乐观更新：先获取当前状态用于可能的回滚
    const currentProjects = _get().projects
    const projectIndex = currentProjects.findIndex((p) => p.id === id)
    const deletedProject = currentProjects[projectIndex]
    
    // 立即从UI移除（乐观更新）
    set((state) => ({ 
      projects: state.projects.filter((p) => p.id !== id),
      error: null
    }))
    
    // 后台执行实际删除
    try {
      await projectService.deleteProject(id)
    } catch (error) {
      if (deletedProject && projectIndex !== -1) {
        set((state) => {
          if (state.projects.some(p => p.id === deletedProject.id)) {
            return { error: error instanceof Error ? error.message : '删除项目失败' }
          }
          const newProjects = [...state.projects]
          newProjects.splice(projectIndex, 0, deletedProject)
          return { 
            projects: newProjects,
            error: error instanceof Error ? error.message : '删除项目失败，已恢复'
          }
        })
      } else {
        set({ error: error instanceof Error ? error.message : '删除项目失败' })
      }
    }
  },

  batchDeleteProjects: async (ids: number[]) => {
    const currentProjects = _get().projects

    set((state) => ({
      projects: state.projects.filter((p) => !ids.includes(p.id)),
      error: null,
    }))

    try {
      const result = await projectService.batchDeleteProjects(ids)

      if (result.failed.length > 0) {
        const failedIds = new Set(result.failed.map((f) => f.id))
        const toRestore = currentProjects.filter((p) => failedIds.has(p.id))
        set((state) => ({
          projects: [...state.projects, ...toRestore],
          error: `${result.deleted.length} 个项目删除成功，${result.failed.length} 个失败`,
        }))
      }
      return result
    } catch (error) {
      set({
        projects: currentProjects,
        error: error instanceof Error ? error.message : '批量删除失败，已恢复',
      })
      throw error
    }
  },
}))

useAuthStore.subscribe((state, prevState) => {
  const prevUserId = prevState.user?.id ?? null
  const nextUserId = state.user?.id ?? null

  if (!shouldResetProjectStateForAuthChange(prevUserId, nextUserId, state.isAuthenticated)) {
    return
  }

  useProjectStore.setState({
    projects: [],
    currentProject: null,
    loading: false,
    hasLoadedOnce: false,
    loadedForUserId: nextUserId,
    error: null,
  })
})
