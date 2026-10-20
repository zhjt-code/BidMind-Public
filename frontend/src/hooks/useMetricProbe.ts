import { useEffect, useRef } from 'react'

/**
 * Phase 6: 前端埋点 hook.
 *
 * 自动采集:
 *   - web_first_paint_seconds: 首屏加载耗时
 *   - web_page_mount_total: 页面挂载计数
 *   - web_page_abort_total: 页面提前离开 (unmount 小于 3s)
 *
 * 批量聚合 + 延迟上报, 单次请求不超过 200 条事件.
 * 调用方::
 *
 *    useMetricProbe({ page: 'FileUpload' })
 */

interface MetricProbeEvent {
  name: string
  value: number
  labels?: Record<string, string>
  ts?: number
}

const _queue: MetricProbeEvent[] = []
let _flushTimer: number | null = null

function _scheduleFlush() {
  if (_flushTimer !== null) return
  _flushTimer = window.setTimeout(async () => {
    _flushTimer = null
    if (_queue.length === 0) return
    const batch = _queue.splice(0, 100)
    try {
      await fetch('/api/admin/ops/metrics/frontend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ events: batch }),
      })
    } catch {
      // 失败静默, 不影响业务
    }
  }, 5000)
}

export function reportMetric(name: string, value: number = 1, labels?: Record<string, string>) {
  _queue.push({ name, value, labels, ts: Date.now() / 1000 })
  _scheduleFlush()
}

interface UseMetricProbeOptions {
  page: string
  firstPaint?: boolean
}

export function useMetricProbe(opts: UseMetricProbeOptions) {
  const mountAt = useRef<number>(performance.now())

  useEffect(() => {
    reportMetric('web_page_mount_total', 1, { page: opts.page })

    if (opts.firstPaint !== false) {
      const paintEntries = performance.getEntriesByType('paint')
      const fcp = paintEntries.find((e) => e.name === 'first-contentful-paint')
      if (fcp) {
        reportMetric('web_first_paint_seconds', fcp.startTime / 1000, { page: opts.page })
      }
    }

    return () => {
      const duration = (performance.now() - mountAt.current) / 1000
      if (duration < 3) {
        reportMetric('web_page_abort_total', 1, { page: opts.page })
      }
      reportMetric('web_page_duration_seconds', duration, { page: opts.page })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

export function reportApiFailure(endpoint: string) {
  reportMetric('web_api_failure_total', 1, { endpoint: endpoint.slice(0, 64) })
}

export function reportCriticalAction(action: string, durationMs: number) {
  reportMetric('web_critical_action_seconds', durationMs / 1000, { action: action.slice(0, 64) })
}
