import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { llmCenterService } from '@/services/llmCenterApi'

interface TraceRow {
  id: number
  node_key: string
  project_id: number | null
  call_count: number
  total_latency_ms: number
  error_count: number
  model_name: string | null
  created_at: string
}

interface TracesTabProps {
  nodeKey: string
}

export const TracesTab: React.FC<TracesTabProps> = ({ nodeKey }) => {
  const [traces, setTraces] = useState<TraceRow[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    llmCenterService.getExecutionTraces(nodeKey)
      .then(setTraces)
      .catch(() => toast.error('加载轨迹失败'))
      .finally(() => setLoading(false))
  }, [nodeKey])

  if (loading) return <div className="flex justify-center py-8"><div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>

  if (traces.length === 0) return <p className="text-sm text-gray-400 italic py-4">暂无调用记录</p>

  return (
    <div className="space-y-2">
      {traces.map(t => (
        <div key={t.id} className="p-3 rounded-lg border border-gray-200 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-mono text-gray-700">{t.node_key}</span>
            <span className="text-gray-400">{new Date(t.created_at).toLocaleDateString()}</span>
          </div>
          <div className="flex gap-4 mt-1.5 text-gray-500">
            <span>调用 <strong className="text-gray-700">{t.call_count}</strong> 次</span>
            <span>延迟 <strong className="text-gray-700">{Math.round(t.total_latency_ms / Math.max(t.call_count, 1))}ms</strong> (avg)</span>
            {t.error_count > 0 && <span className="text-red-500">错误 {t.error_count}</span>}
            {t.model_name && <span>{t.model_name}</span>}
            {t.project_id && <span>项目#{t.project_id}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}
