import React, { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { llmCenterService, type LlmNodeClass, type LlmNodeSummary } from '@/services/llmCenterApi'
import { CHANNEL_COLORS, NODE_CLASS_COLORS, NODE_CLASS_LABELS } from '../lib/constants'

interface NodeCardProps {
  node: LlmNodeSummary
  onClick: () => void
  onClassChanged?: () => void
}

function isNodeClassLocked(node: LlmNodeSummary): boolean {
  return node.execution_channel === 'vision' || node.node_key.startsWith('judge.')
}

export const NodeCard: React.FC<NodeCardProps> = ({ node, onClick, onClassChanged }) => {
  const [nodeClass, setNodeClass] = useState<LlmNodeClass>(node.node_class ?? 'judgment')
  const [updating, setUpdating] = useState(false)
  const locked = isNodeClassLocked(node)

  useEffect(() => {
    setNodeClass(node.node_class ?? 'judgment')
  }, [node.node_key, node.node_class])

  const handleClassClick = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (locked || updating) return

    const next: LlmNodeClass = nodeClass === 'mechanical' ? 'judgment' : 'mechanical'
    setUpdating(true)
    try {
      await llmCenterService.updateNodeConfig(node.node_key, { node_class: next })
      setNodeClass(next)
      toast.success(`已切换为${NODE_CLASS_LABELS[next]}`)
      onClassChanged?.()
    } catch {
      toast.error('节点分类切换失败')
    } finally {
      setUpdating(false)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className={`w-full text-left p-3 rounded-lg border hover:border-primary/40 hover:shadow-sm transition-all bg-white group relative cursor-pointer ${
        node.has_draft ? 'border-orange-300 ring-1 ring-orange-200' : 'border-gray-200'
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5 min-w-0">
          {node.has_draft && (
            <span className="shrink-0 w-2 h-2 rounded-full bg-orange-400" title="有未发布的草稿" />
          )}
          <span className="text-sm font-medium text-gray-900 group-hover:text-primary truncate">
            {node.name}
          </span>
        </div>
        {node.execution_channel === 'vision' && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 ${CHANNEL_COLORS.vision}`}>
            vision
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <span className="font-mono">{node.call_method}</span>
        <span>T={node.default_temperature}</span>
        {node.active_version != null && (
          <span className="text-green-600">v{node.active_version}</span>
        )}
        {node.has_draft && (
          <span className="text-orange-500 font-medium">draft</span>
        )}
      </div>
      {node.description && (
        <p className="mt-1 text-xs text-gray-400 line-clamp-1 pr-16">{node.description}</p>
      )}
      <span
        title={locked ? '该节点分类不可修改' : '点击切换节点分类'}
        onClick={handleClassClick}
        className={`absolute bottom-2 right-2 text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 ${
          locked
            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
            : `${NODE_CLASS_COLORS[nodeClass]} cursor-pointer hover:opacity-80`
        } ${updating ? 'opacity-60 pointer-events-none' : ''}`}
      >
        {NODE_CLASS_LABELS[nodeClass]}
      </span>
    </div>
  )
}
