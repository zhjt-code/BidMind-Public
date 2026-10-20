import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import {
  normalizeProjectDashboardResponse,
  type ProjectDashboardData,
  type ProjectDashboardResponse,
} from './projectDashboardApi'

/**
 * 职责: 拉取项目仪表盘并交给已有归一化函数。
 * 不做: 不定义仪表盘字段，不渲染页面。
 */
export const projectDashboardService = {
  getDashboard: async (trendWindowDays = 30, healthWindowDays = 30): Promise<ProjectDashboardData> => {
    const response = await api.get<ApiResponse<ProjectDashboardResponse>>(
      `/projects/dashboard?window_days=${trendWindowDays}&health_window_days=${healthWindowDays}`,
    )
    return normalizeProjectDashboardResponse(response.data)
  },
}
