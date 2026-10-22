import React, { useMemo } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import type { LlmStageInfo, LlmEdgeInfo } from '@/services/llmCenterApi'
import type { ViewMode } from '../types'
import { buildFlowLanes } from '../lib/buildFlowLanes'
import { NodeCard } from './NodeCard'

interface StageAccordionProps {
  stage: LlmStageInfo
  viewMode: ViewMode
  edges: LlmEdgeInfo[]
  expanded: boolean
  onToggle: () => void
  onNodeClick: (nodeKey: string) => void
}

export const StageAccordion: React.FC<StageAccordionProps> = ({
  stage, viewMode, edges, expanded, onToggle, onNodeClick,
}) => {
  const filteredNodes = viewMode === 'business'
    ? stage.nodes.filter(n => n.visibility === 'main')
    : stage.nodes

  const lanes = useMemo(
    () => viewMode === 'technical' ? buildFlowLanes(filteredNodes, edges) : [],
    [filteredNodes, edges, viewMode],
  )

  if (filteredNodes.length === 0) return null

  return (
    <div className="mb-4">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded hover:bg-gray-50"
      >
        {expanded ? <ChevronDown className="h-4 w-4 text-gray-400" /> : <ChevronRight className="h-4 w-4 text-gray-400" />}
        <span className="text-sm font-semibold text-gray-700">{stage.name}</span>
        {filteredNodes.some(n => n.has_draft) && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-600 font-medium">
            {filteredNodes.filter(n => n.has_draft).length} 待处理
          </span>
        )}
        <span className="text-xs text-gray-400 ml-auto">{filteredNodes.length} nodes</span>
      </button>
      {expanded && (
        viewMode === 'technical' && lanes.length > 0 ? (
          <div className="mt-2 pl-6 space-y-2">
            {lanes.map((lane, li) => (
              <div key={li} className="flex flex-wrap gap-2">
                {lane.map(n => (
                  <div key={n.node_key} className="w-56 shrink-0">
                    <NodeCard node={n} onClick={() => onNodeClick(n.node_key)} />
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-2 pl-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {filteredNodes.map(n => (
              <NodeCard key={n.node_key} node={n} onClick={() => onNodeClick(n.node_key)} />
            ))}
          </div>
        )
      )}
    </div>
  )
}
