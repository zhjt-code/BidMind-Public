/**
 * 文档解析进度轮询 hook（CollusionUpload + FileUpload 共用）。
 *
 * 抽自 CollusionUpload.tsx + FileUpload.tsx 的同源 startExistingDocPolling /
 * startPolling / markDocumentRetryPolling 三处实现。两页业务差异通过依赖注入
 * 表达：
 *   - getInterval 注入自适应间隔策略（CollusionUpload 用默认；FileUpload 传
 *     getPollingInterval(s, true) 包含 vectorizing 8s 差异）
 *   - onRefreshRetryState 注入失败后刷新重试状态的入口
 *     （CollusionUpload 用 collusionService.getRetryState；FileUpload 用
 *     reviewService.getRetryState）
 *
 * 行为保持与原局部实现 byte-equal：
 *   1. 启动时立即 poll 一次（与原 startExistingDocPolling / startPolling 一致）
 *   2. data.status === 'completed' → onCompleted + 删除 timer
 *   3. data.status === 'failed' → onFailed(error || '处理失败') + 触发 onRefreshRetryState
 *   4. 进行中 → setTimeout(poll, getInterval(data.stage))
 *   5. catch → setTimeout(poll, 3000)（兜底节奏不变）
 *   6. unmount → 清理所有 timer，后续 setState 短路
 *
 * 关键实现约束（破坏将导致行为退化）：
 *   - mountedRef / pollingRef 必须用 useRef 保活
 *   - onRefreshRetryStateRef / getIntervalRef 用 useRef + useEffect 同步，
 *     避免 setTimeout 闭包陈旧
 *   - 同 pollKey 重复 startPoll 直接 return（与原局部实现幂等行为一致）
 *
 * 核心 tick 逻辑 runOneDocumentPollTick 抽为独立纯函数（documentPollTick.ts），
 * 便于 node:test 单测覆盖而不引入 React 渲染基础设施。
 */
import { useCallback, useEffect, useRef } from 'react'
import { documentService } from '@/services/documentApi'
import type { ProcessStatusData } from '@/services/documentApi'
import { runOneDocumentPollTick } from './documentPollTick'

export interface DocumentPollOptions {
  /** 当前轮询的文档 ID */
  documentId: number
  /** 唯一标识此次轮询任务（同 key 已存在时 startPoll 幂等） */
  pollKey: string
  /** 每次成功拿到 status 后的同步回调（用于更新页面进度状态） */
  onTick: (data: ProcessStatusData) => void
  /** 文档解析完成（status='completed'）的终态回调 */
  onCompleted: () => void
  /** 文档解析失败（status='failed'）的终态回调，参数为错误消息 */
  onFailed: (errorMessage: string) => void
}

export interface UseDocumentProcessPollingOptions {
  /** 项目 ID。null 时 startPoll 直接 return（与原 if (!projectId) return 行为一致） */
  projectId: number | null
  /** 自适应间隔策略；CollusionUpload 用 getPollingInterval；FileUpload 用 getPollingInterval(_, true) */
  getInterval: (stage: string) => number
  /** 失败时刷新重试状态。CollusionUpload 传 collusionService.getRetryState；FileUpload 传 reviewService.getRetryState */
  onRefreshRetryState: () => Promise<void>
}

export interface DocumentProcessPollingController {
  /** 启动一次轮询；同 pollKey 已运行时幂等返回 */
  startPoll: (options: DocumentPollOptions) => void
  /** 主动停止指定 pollKey 的轮询并删除 timer */
  stopPoll: (pollKey: string) => void
  /** 是否有活跃 poll（供调用方做幂等判断） */
  hasActivePoll: (pollKey: string) => boolean
}

export function useDocumentProcessPolling(
  options: UseDocumentProcessPollingOptions,
): DocumentProcessPollingController {
  const { projectId, getInterval, onRefreshRetryState } = options

  const pollingRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  const mountedRef = useRef(true)

  const onRefreshRetryStateRef = useRef(onRefreshRetryState)
  const getIntervalRef = useRef(getInterval)
  useEffect(() => {
    onRefreshRetryStateRef.current = onRefreshRetryState
    getIntervalRef.current = getInterval
  }, [onRefreshRetryState, getInterval])

  const clearPollKey = useCallback((pollKey: string) => {
    const handle = pollingRef.current[pollKey]
    if (handle) {
      clearTimeout(handle)
      delete pollingRef.current[pollKey]
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    const handles = pollingRef.current
    return () => {
      mountedRef.current = false
      for (const key of Object.keys(handles)) {
        clearTimeout(handles[key])
        delete handles[key]
      }
    }
  }, [])

  const startPoll = useCallback(
    (pollOpts: DocumentPollOptions) => {
      if (projectId == null) return
      const { documentId, pollKey, onTick, onCompleted, onFailed } = pollOpts
      if (pollingRef.current[pollKey]) return

      const tick = async () => {
        clearPollKey(pollKey)
        await runOneDocumentPollTick({
          projectId,
          documentId,
          fetchStatus: documentService.getProcessStatus,
          getInterval: getIntervalRef.current,
          onTick,
          onCompleted,
          onFailed,
          onRefreshRetryState: () => onRefreshRetryStateRef.current(),
          scheduleNext: (delayMs) => {
            pollingRef.current[pollKey] = setTimeout(tick, delayMs)
          },
          isMounted: () => mountedRef.current,
        })
      }

      void tick()
    },
    [projectId, clearPollKey],
  )

  const stopPoll = useCallback(
    (pollKey: string) => {
      clearPollKey(pollKey)
    },
    [clearPollKey],
  )

  const hasActivePoll = useCallback((pollKey: string) => {
    return pollingRef.current[pollKey] != null
  }, [])

  return { startPoll, stopPoll, hasActivePoll }
}
