import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { llmCenterService } from '@/services/llmCenterApi'

interface HistoryRecord {
  id: number
  action: string
  published_by: string
  template_version_id: number
  change_note: string | null
  created_at: string
}

interface HistoryTabProps {
  nodeKey: string
}

export const HistoryTab: React.FC<HistoryTabProps> = ({ nodeKey }) => {
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    llmCenterService.getPublishHistory(nodeKey)
      .then(setRecords)
      .catch(() => toast.error('加载发布历史失败'))
      .finally(() => setLoading(false))
  }, [nodeKey])

  if (loading) return <div className="flex justify-center py-8"><div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>

  if (records.length === 0) return <p className="text-sm text-gray-400 italic py-4">暂无发布记录</p>

  return (
    <div className="space-y-2">
      {records.map(r => (
        <div key={r.id} className="p-3 rounded-lg border border-gray-200 text-xs">
          <div className="flex items-center gap-2">
            <span className={`px-1.5 py-0.5 rounded-full font-medium ${r.action === 'publish' ? 'bg-green-100 text-green-700' : 'bg-orange-100 text-orange-700'}`}>
              {r.action === 'publish' ? '发布' : '回滚'}
            </span>
            <span className="text-gray-600">by {r.published_by}</span>
            <span className="ml-auto text-gray-400">{new Date(r.created_at).toLocaleString()}</span>
          </div>
          {r.change_note && <p className="text-gray-500 mt-1">{r.change_note}</p>}
        </div>
      ))}
    </div>
  )
}
