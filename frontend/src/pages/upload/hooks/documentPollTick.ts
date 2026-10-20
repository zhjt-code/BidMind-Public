/**
 * 文档解析轮询核心 tick 纯函数（0 React/service 依赖）。
 *
 * 抽自 CollusionUpload.tsx + FileUpload.tsx 的 startExistingDocPolling /
 * startPolling 内嵌 poll 闭包；与原局部实现 byte-equal 等价。
 *
 * 单独成文件的目的：让 node:test 单测能 import 该模块而不触发
 * `@/services/...` 运行时依赖链（hook 入口 useDocumentProcessPolling 才需要
 * documentService）。
 */
import type { ProcessStatusData } from '@/services/documentApi'

/** runOneDocumentPollTick 的执行上下文，全部依赖通过参数注入便于单测 */
export interface PollTickContext {
  projectId: number
  documentId: number
  fetchStatus: (
    projectId: number,
    documentId: number,
  ) => Promise<{ data: ProcessStatusData }>
  getInterval: (stage: string) => number
  onTick: (data: ProcessStatusData) => void
  onCompleted: () => void
  onFailed: (errorMessage: string) => void
  onRefreshRetryState: () => Promise<void>
  scheduleNext: (delayMs: number) => void
  isMounted: () => boolean
}

/** 单次 poll tick 的输出结果 */
export type PollTickOutcome = 'completed' | 'failed' | 'continue' | 'unmounted'

/**
 * 单次 poll tick 的纯函数实现。
 *
 * 行为流程（与原 CollusionUpload.startPolling / startExistingDocPolling 内
 * 嵌 poll 闭包 byte-equal）：
 *   1. 入口检查 isMounted；false → 'unmounted'
 *   2. fetchStatus 后再次检查 isMounted；false → 'unmounted'
 *   3. data.status === 'completed' → onCompleted → 'completed'
 *   4. data.status === 'failed' → onFailed(error || '处理失败') +
 *      void onRefreshRetryState() → 'failed'
 *   5. 进行中 → scheduleNext(getInterval(stage)) → 'continue'
 *   6. catch + isMounted → scheduleNext(3000) → 'continue'
 *   7. catch + !isMounted → 不调度 → 'continue'
 */
export async function runOneDocumentPollTick(
  ctx: PollTickContext,
): Promise<PollTickOutcome> {
  if (!ctx.isMounted()) return 'unmounted'
  try {
    const result = await ctx.fetchStatus(ctx.projectId, ctx.documentId)
    const data = result.data
    if (!ctx.isMounted()) return 'unmounted'
    ctx.onTick(data)

    if (data.status === 'completed') {
      ctx.onCompleted()
      return 'completed'
    }
    if (data.status === 'failed') {
      ctx.onFailed(data.error || '处理失败')
      void ctx.onRefreshRetryState()
      return 'failed'
    }
    ctx.scheduleNext(ctx.getInterval(data.stage))
    return 'continue'
  } catch {
    if (ctx.isMounted()) ctx.scheduleNext(3000)
    return 'continue'
  }
}
