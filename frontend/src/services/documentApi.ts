import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

export interface ProcessStageData {
  key: string
  label: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress?: number
  total?: number
  // 纯文案心跳可达前端 (例如 "目录解析完成" / "页面降噪完成"),
  // 避免 chunking 长时间显示 "处理中..." 被误判卡死。
  detail?: string
}

// ARQ 状态机精细状态: 后端 vector_job_states.status 字段取值
// 未启用 ARQ 时后端可能返回 null / pending
export type VectorJobStatus =
  | 'pending'
  | 'queued'
  | 'claimed'
  | 'running'
  | 'completed'
  | 'failed'
  | 'lost'
  | 'recovering'

export interface ProcessStatusData {
  status: 'processing' | 'completed' | 'failed'
  stage: string
  stage_label: string
  progress: number
  total: number
  overall_status?: 'idle' | 'running' | 'completed' | 'failed'
  overall_progress?: number
  message?: string
  recognized?: number
  attempted?: number
  failed_pending?: number
  retry_round?: number
  vl_phase?: 'round1_running' | 'round1_done' | 'retry_running' | 'done'
  vl_current_round?: number
  vl_round_done?: number
  vl_round_total?: number
  vl_retry_pending?: number
  vl_unrecognized_final?: number
  manual_review_required?: boolean
  unrecognized_images?: number
  unrecognized_pages?: number
  vl_concurrency?: number
  vl_p95_ms?: number
  vl_adjust_desc?: string
  error?: string
  stages: ProcessStageData[]
  steps?: ProcessStageData[]
  vl_skipped?: boolean
  // 向量化降级状态, 前端据此渲染 "CPU降级运行中" 徽标,
  // 避免静默慢被误判为卡死。
  degraded_to_cpu?: boolean
  fallback_reason?: string
  vectorizing_substage?: string
  recovery_mode?: string
  // ARQ 状态机精细字段 (后端已暴露, 前端可选消费)
  job_status?: VectorJobStatus | null
  queue_mode?: 'arq' | 'asyncio' | null
  arq_job_id?: string | null
  worker_id?: string | null
}

// 文档门禁: 解析前要素校验结论
export type DocGateVerdict = 'pass' | 'suspect' | 'unreadable' | 'encrypted' | 'corrupt' | 'empty'

export interface DocGateResult {
  verdict: DocGateVerdict
  score: number
  doc_type: string
  hit_features: string[]
  missing_features: string[]
  reasons: string[]
  sampled_pages: number
  page_count: number
  toc_entry_count: number
  has_confident_toc: boolean
  has_long_document_fallback: boolean
  engine: string
}

export interface DocumentUploadData {
  id: number
  project_id: number
  doc_type: string
  /** 投标信封卷角色: null=整本 / commercial=商务标 / technical=技术标 */
  bid_part?: string | null
  filename: string
  file_size: number
  message: string
  gate_result?: DocGateResult | null
}

export const documentService = {
  uploadFile: async (
    projectId: number,
    file: File,
    docType: 'tender' | 'bid',
    bidPart?: string | null,
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('doc_type', docType)
    // 投标信封分卷: commercial(商务标) / technical(技术标), 缺省=整本
    if (bidPart) formData.append('bid_part', bidPart)

    return api.post<ApiResponse<DocumentUploadData>>(`/projects/${projectId}/files`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 300000, // 大文件上传，5 分钟超时
    })
  },

  getDocuments: async (projectId: number) => {
    return api.get(`/projects/${projectId}/files`)
  },

  processDocument: async (
    projectId: number,
    documentId: number,
    skipVl?: boolean,
    gateAck?: boolean,
  ) => {
    const query = [
      ...(skipVl ? ['skip_vl=true'] : []),
      ...(gateAck ? ['gate_ack=true'] : []),
    ].join('&')
    const params = query ? `?${query}` : ''
    return api.post(`/projects/${projectId}/files/${documentId}/process${params}`, null, {
      timeout: 30000,
    })
  },

  deleteDocument: async (projectId: number, documentId: number) => {
    return api.delete(`/projects/${projectId}/files/${documentId}`, {
      timeout: 30000,
    })
  },

  resetBidReviewUpload: async (projectId: number) => {
    return api.post(`/projects/${projectId}/files/reset-upload`, null, {
      timeout: 30000,
    })
  },

  getProcessStatus: async (projectId: number, documentId: number) => {
    return api.get<ApiResponse<ProcessStatusData>>(`/projects/${projectId}/files/${documentId}/process/status`, {
      timeout: 10000,
    })
  },

  getPreviewUrl: (projectId: number, documentId: number): string =>
    `/api/projects/${projectId}/files/${documentId}/preview`,
}
