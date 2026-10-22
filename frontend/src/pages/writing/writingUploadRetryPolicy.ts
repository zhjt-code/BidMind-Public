/**
 * 标书上传/处理流程重试策略 (方向 C：POST 与轮询分离, 以轮询终态为唯一真相)
 *
 * 背景 :
 * WritingUpload.tsx 原本把 `documentService.processDocument` POST 的 30s timeout
 * 当成"整个业务流失败"，直接 `setStage('error')` 弹"重新上传"。但后端
 * `POST /files/{id}/process` 只是把任务入队, 连接超时 ≠ 任务未执行:
 * - ARQ worker 可能已经领取 job 并在跑 (见 safe_encode_batch 降级场景)
 * - pollUploadStatus 会最终看到 status=parsing → extracted 正常推进
 * 因此"POST 失败"应该区分:
 * - 可重试的网络/瞬态错误 (timeout, Network Error, 5xx 维护) → 进入轮询
 * - 真正的业务错误 (4xx 鉴权/参数错, 余额不足等) → 立即判失败
 *
 * 同理 pollUploadStatus 的容错也不能按"失败次数"简单门槛, 应按"连续无推进
 * 时长"衡量：只要后端心跳还有进展 (currentStage / overallProgress 变化)
 * 就容忍偶发的 30s 超时; 只有长时间没有任何推进 + 仍在报错, 才视为失败。
 *
 * 本模块只放纯函数 (无 React/DOM 依赖), 以便 node:test 单测独立覆盖。
 */

/** 轮询错误判决 */
export type PollErrorVerdict =
  | { kind: 'retry'; reason: string; isTimeout: boolean }
  | { kind: 'fatal'; reason: string }

/** 连续无推进多久判定真失败 (默认 3 分钟, 覆盖 GPU stall + CPU 回退 2-3 分钟场景) */
export const POLL_MAX_NO_ADVANCE_MS = 180_000

/** 匹配 axios 默认超时消息 "timeout of 30000ms exceeded" 或其他含 timeout 的提示 */
const TIMEOUT_PATTERN = /timeout(\s+of\s+\d+\s*ms)?\s+exceeded|请求超时|timeout/i
/** axios "Network Error" 或后端转写的 "无法连接到服务器" */
const NETWORK_PATTERN = /network\s+error|无法连接|连接失败|ECONNREFUSED|ECONNRESET/i
/** 维护/升级类提示 (见 services/httpClient.ts 拦截器对 502/503/504 的封装) */
const MAINTENANCE_PATTERN = /维护|升级|service unavailable|bad gateway|gateway timeout/i
/** 明确表达业务失败的语义关键词 */
const BUSINESS_FAILURE_PATTERN = /约束提取失败|处理恢复失败|余额不足|权限不足|未找到|重试.*次|文档处理恢复失败/

function errorMessage(err: unknown, fallback = '未知错误'): string {
  if (err instanceof Error) return err.message || fallback
  if (typeof err === 'string') return err || fallback
  return fallback
}

/**
 * POST 是否为"可乐观重试"的瞬态错误:
 * - true: 不应该当成业务失败, 交给轮询确认后端任务状态
 * - false: 真正的客户端/业务错误, 调用方应该立即抛出
 */
export function isRetryableNetworkError(err: unknown): boolean {
  const message = errorMessage(err)
  if (BUSINESS_FAILURE_PATTERN.test(message)) return false
  return (
    TIMEOUT_PATTERN.test(message) ||
    NETWORK_PATTERN.test(message) ||
    MAINTENANCE_PATTERN.test(message)
  )
}

/**
 * 对 pollUploadStatus 捕获的错误做判决。
 *
 * @param err 被 catch 的错误
 * @param noAdvanceMs 距上次任务状态推进的毫秒数
 * @param maxNoAdvanceMs 允许的最长"连续无推进"时长; 超过则无论何种错误均视为 fatal
 */
export function classifyPollError(
  err: unknown,
  noAdvanceMs: number,
  maxNoAdvanceMs: number = POLL_MAX_NO_ADVANCE_MS,
): PollErrorVerdict {
  const message = errorMessage(err, '请求失败')

  if (BUSINESS_FAILURE_PATTERN.test(message)) {
    return { kind: 'fatal', reason: message }
  }

  const isTimeout = TIMEOUT_PATTERN.test(message)
  const isRetryable = isTimeout || NETWORK_PATTERN.test(message) || MAINTENANCE_PATTERN.test(message)

  if (noAdvanceMs > maxNoAdvanceMs) {
    return {
      kind: 'fatal',
      reason: `任务已连续 ${Math.round(noAdvanceMs / 1000)}s 无进展 (最近错误: ${message})`,
    }
  }

  if (isRetryable) {
    return { kind: 'retry', reason: message, isTimeout }
  }

  return { kind: 'retry', reason: message, isTimeout: false }
}

/**
 * 为 stage text 补充"等待后端确认"提示, 便于 UX 上区分"真卡住"与"乐观等待"。
 */
export function decorateWaitingText(base: string, context: 'post_timeout' | 'poll_retry'): string {
  const suffix = context === 'post_timeout' ? '（等待后端确认）' : '（自动重试中）'
  if (!base) return suffix
  if (base.endsWith('）') || base.includes('（')) return base
  return `${base}${suffix}`
}

/**
 * 基于状态原始字段计算一个单调比较 key, 用来识别"任务是否推进"。
 * 只要 key 与上次不同就视为推进 (reset noAdvance 计时器)。
 */
export function computeProgressKey(raw: {
  status?: string
  currentStage?: string
  overallProgress?: number
}): string {
  const progress = typeof raw.overallProgress === 'number' ? raw.overallProgress : -1
  return `${raw.status ?? ''}|${raw.currentStage ?? ''}|${progress}`
}
