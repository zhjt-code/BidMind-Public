import axios from 'axios'

export const SYSTEM_MAINTENANCE_MESSAGE = '系统正在升级维护中，请稍后重试'

export class ApiRequestError extends Error {
  statusCode?: number
  /** machine-readable 业务错误码，如 INSUFFICIENT_BALANCE */
  errorCode?: string
  /** 结构化错误附加字段，如 { available, required } */
  details?: Record<string, unknown>

  constructor(
    message: string,
    statusCode?: number,
    options?: { errorCode?: string; details?: Record<string, unknown> },
  ) {
    super(message)
    this.name = 'ApiRequestError'
    this.statusCode = statusCode
    this.errorCode = options?.errorCode
    this.details = options?.details
  }
}

export interface StructuredApiErrorPayload {
  errorCode: string
  message: string
  details: Record<string, unknown>
}

/**
 * 从后端错误响应体提取结构化错误。
 * 兼容 {detail: {error_code, message, ...}} 与顶层 {error_code, ...} 两种形态；
 * 无 error_code 时返回 null，调用方回退字符串路径。
 */
export function extractStructuredApiError(payload: unknown): StructuredApiErrorPayload | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null
  }
  const record = payload as Record<string, unknown>
  const nested = record.detail
  const detail = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : record

  const errorCode = detail.error_code
  if (typeof errorCode !== 'string' || !errorCode) {
    return null
  }
  const { error_code: _omitted, message, ...rest } = detail
  void _omitted
  return {
    errorCode,
    message: typeof message === 'string' && message ? message : '请求失败',
    details: rest,
  }
}

const GENERIC_GATEWAY_TEXTS = new Set([
  'Bad Gateway',
  'Gateway Timeout',
  'Service Unavailable',
])

function normalizeText(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }

  const lower = trimmed.toLowerCase()
  if (lower.startsWith('<!doctype html') || lower.startsWith('<html')) {
    return null
  }

  if (GENERIC_GATEWAY_TEXTS.has(trimmed)) {
    return null
  }

  return trimmed
}

export function extractApiErrorMessage(payload: unknown, fallback = '请求失败'): string {
  if (typeof payload === 'string') {
    return normalizeText(payload) ?? fallback
  }

  if (payload instanceof Error) {
    return normalizeText(payload.message) ?? fallback
  }

  if (Array.isArray(payload)) {
    const parts = payload
      .map(item => extractApiErrorMessage(item, ''))
      .filter(Boolean)
    return parts.length > 0 ? parts.join('; ') : fallback
  }

  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>

    for (const candidate of [record.detail, record.message, record.msg]) {
      const message = extractApiErrorMessage(candidate, '')
      if (message) {
        return message
      }
    }

    if (import.meta.env?.DEV) {
      try {
        return JSON.stringify(payload)
      } catch {
        return fallback
      }
    }
    return fallback
  }

  return fallback
}

export function shouldShowMaintenanceMessage(status: number | undefined, payload: unknown): boolean {
  if (status !== 502 && status !== 503 && status !== 504) {
    return false
  }

  return extractApiErrorMessage(payload, '') === ''
}

export function getReadableErrorMessage(error: unknown, fallback = '请求失败'): string {
  if (axios.isAxiosError(error)) {
    const payloadMessage = extractApiErrorMessage(error.response?.data, '')
    if (payloadMessage) {
      return payloadMessage
    }

    return normalizeText(error.message) ?? fallback
  }

  if (error instanceof Error) {
    return normalizeText(error.message) ?? fallback
  }

  return fallback
}
