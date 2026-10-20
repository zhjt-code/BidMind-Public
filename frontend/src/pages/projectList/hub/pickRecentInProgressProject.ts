/**
 * 职责: 从域内项目中选出「继续上次」候选（最近更新且未完成）
 * 不做: 不做路由跳转、不改项目状态
 */
import type { Project } from '@/stores/projectStore'

export function pickRecentInProgressProject(projects: Project[]): Project | null {
  const candidates = projects.filter((project) => project.status !== 'completed')
  if (candidates.length === 0) return null

  return [...candidates].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  )[0] ?? null
}
