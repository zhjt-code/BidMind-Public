import React, { useState } from 'react'
import type { OpsMetricItem, OpsOverviewResponse } from '@/services/opsApi'
import LlmReliabilityPanel from './components/LlmReliabilityPanel'
import MetricCard from './components/MetricCard'
import { OPS_LAYER_LABELS } from './labels'
import { isLayerCardMuted } from './dutyLogic'
import HotspotPanel from './components/HotspotPanel'
import WeeklyAdviceCard from './components/WeeklyAdviceCard'
import CatalogPanel from './components/CatalogPanel'

export interface LabPaneProps {
  window: string
  overview: OpsOverviewResponse | undefined
}

const LabPane: React.FC<LabPaneProps> = ({ window, overview }) => {
  const layersMap = overview?.layers ?? {}
  const [activeLayer, setActiveLayer] = useState('platform')

  return (
    <div className="space-y-6">
      <LlmReliabilityPanel window={window} />
      <WeeklyAdviceCard />
      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-2.5 text-sm font-medium text-gray-700 border-b border-gray-200">分层指标</div>
        <div className="flex overflow-x-auto border-b border-gray-200">
          {Object.keys(layersMap).map((layer) => {
            const items = layersMap[layer] || []
            const warnCount = items.filter((m) => m.verdict === 'warn' || m.verdict === 'critical').length
            return (
              <button
                key={layer}
                type="button"
                onClick={() => setActiveLayer(layer)}
                className={`px-4 py-2.5 text-sm whitespace-nowrap border-b-2 ${
                  activeLayer === layer ? 'border-primary text-primary font-medium' : 'border-transparent text-gray-600'
                }`}
              >
                {OPS_LAYER_LABELS[layer] || layer}
                <span className="ml-1.5 text-xs text-gray-400">{items.length}</span>
                {warnCount > 0 && (
                  <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-100 px-1 text-[10px] font-medium text-rose-700">
                    {warnCount}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        <div className="p-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(layersMap[activeLayer] || []).map((metric: OpsMetricItem) => (
            <MetricCard key={metric.name} metric={metric} muted={isLayerCardMuted(metric)} />
          ))}
        </div>
      </div>
      <HotspotPanel window={window} />
      <CatalogPanel />
    </div>
  )
}

export default LabPane
