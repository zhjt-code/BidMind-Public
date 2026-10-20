import { useQuery } from '@tanstack/react-query'
import { projectQueryKey, projectService } from '@/services/projectApi'
import { useProjectStore } from '@/stores/projectStore'

/**
 * 职责: 读取当前流程页已缓存的项目名称，供导出文件名使用。
 * 不做: 不发起下载、不改项目数据。
 */
export function useCachedProjectName(
  projectId: string | number | undefined,
): string | undefined {
  const id = Number(projectId)
  const valid = Number.isFinite(id)
  const cachedProject = useProjectStore((state) => (
    valid ? state.projects.find((project) => project.id === id) : undefined
  ))
  const { data } = useQuery({
    queryKey: projectQueryKey(id),
    queryFn: () => projectService.getProject(id),
    enabled: valid,
    initialData: cachedProject,
  })
  return data?.name
}
