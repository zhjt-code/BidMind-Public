import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import {
  transformDocumentQuality,
  transformFinding,
} from '@/pages/result/documentQuality/documentQualityTransforms'
import type {
  DocumentQualityData,
  QaFinding,
} from '@/pages/result/documentQuality/types'

/**
 * 职责: 文档质检 API 封装(读取视图 + 发现项处置)
 * 不做: 不做筛选/排序(见 documentQualityTransforms)
 */

export const documentQualityService = {
  getDocumentQuality: async (projectId: number): Promise<DocumentQualityData> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/document-quality`,
    )
    const data = (response as ApiResponse<Record<string, unknown>>)?.data
    if (!data) {
      throw new Error('文档质检结果加载失败: 响应缺少数据')
    }
    return transformDocumentQuality(data)
  },

  updateDisposition: async (
    projectId: number,
    findingId: number,
    payload: { disposition: string; note?: string | null; expectedVersion: number },
  ): Promise<QaFinding> => {
    const response = await api.patch<ApiResponse<Record<string, unknown>>>(
      `/projects/${projectId}/document-quality/findings/${findingId}/disposition`,
      {
        disposition: payload.disposition,
        note: payload.note ?? null,
        expected_version: payload.expectedVersion,
      },
    )
    const data = (response as ApiResponse<Record<string, unknown>>)?.data
    if (!data) {
      throw new Error('处置保存失败: 响应缺少数据')
    }
    return transformFinding(data)
  },
}
