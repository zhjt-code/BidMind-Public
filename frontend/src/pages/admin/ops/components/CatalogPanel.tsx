import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { opsService, type MetricCatalogItem } from '@/services/opsApi'
import { Loader2, Package, Code } from 'lucide-react'
import {
  formatDirectionLabel,
  formatLayerLabel,
  formatMetricKindLabel,
  formatPillarLabel,
} from '../labels'

const PILLAR_BG: Record<string, string> = {
  quality: 'bg-indigo-50 text-indigo-700',
  reliability: 'bg-emerald-50 text-emerald-700',
  efficiency: 'bg-sky-50 text-sky-700',
  cost: 'bg-amber-50 text-amber-700',
}

const CatalogPanel: React.FC = () => {
  const [expanded, setExpanded] = React.useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['metrics-catalog'],
    queryFn: () => opsService.getMetricsCatalog(),
    staleTime: 60_000,
    enabled: expanded,
  })

  const items = (data as { items?: MetricCatalogItem[] } | undefined)?.items ?? []

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-50"
      >
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-medium text-gray-700">
            监控指标清单
          </span>
          {data && (
            <span className="text-xs text-gray-400">{items.length} 条</span>
          )}
        </div>
        <span className="text-xs text-gray-400">{expanded ? '收起' : '展开'}</span>
      </button>

      {expanded && (
        <div className="border-t border-gray-200">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 text-gray-500 py-6">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </div>
          ) : items.length === 0 ? (
            <div className="text-sm text-gray-400 text-center py-8">
              当前尚未生成指标目录，请先启动后端服务并完成一次初始化扫描。
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500">
                  <tr>
                    <th className="text-left px-3 py-2">指标编码</th>
                    <th className="text-left px-3 py-2">所属层级</th>
                    <th className="text-left px-3 py-2">关注维度</th>
                    <th className="text-left px-3 py-2">指标类型</th>
                    <th className="text-left px-3 py-2">判定方向</th>
                    <th className="text-left px-3 py-2">标签维度</th>
                    <th className="text-left px-3 py-2">来源代码</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.name} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{item.name}</td>
                      <td className="px-3 py-2 text-xs">
                        <span className="inline-flex px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                          {formatLayerLabel(item.layer)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs">
                        <span className={`inline-flex px-1.5 py-0.5 rounded ${PILLAR_BG[item.pillar] || 'bg-gray-100 text-gray-700'}`}>
                          {formatPillarLabel(item.pillar)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">{formatMetricKindLabel(item.kind)}</td>
                      <td className="px-3 py-2 text-xs text-gray-500">{formatDirectionLabel(item.direction)}</td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {(item.labels || []).join(', ') || '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-400">
                        {item.source.file ? (
                          <span className="inline-flex items-center gap-1" title={`${item.source.module}.${item.source.qualname}`}>
                            <Code className="h-3 w-3" />
                            {item.source.qualname}
                            {item.source.line ? `:${item.source.line}` : ''}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CatalogPanel
