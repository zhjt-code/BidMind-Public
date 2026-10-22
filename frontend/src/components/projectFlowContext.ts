import { buildAppProjectListPath } from '../config/routes.ts'

export type ProjectFlowType = 'bid_review' | 'collusion_check' | 'bid_writing'

export function normalizeProjectFlowType(projectType?: string | null): ProjectFlowType | null {
  if (projectType === 'bid_review' || projectType === 'collusion_check' || projectType === 'bid_writing') {
    return projectType
  }
  return null
}

export function getProjectListReturnPath(projectType?: string | null): string {
  const flowType = normalizeProjectFlowType(projectType)
  return buildAppProjectListPath(flowType ?? undefined)
}

export function getFlowTypeFromPath(pathname: string): ProjectFlowType | null {
  if (/^\/project\/\d+\/writing(?:\/|$)/.test(pathname)) return 'bid_writing'
  if (/^\/project\/\d+\/collusion(?:\/|$)/.test(pathname)) return 'collusion_check'
  if (/^\/project\/\d+\/(?:upload|requirements|progress|result)(?:\/|$)/.test(pathname)) return 'bid_review'
  return null
}
