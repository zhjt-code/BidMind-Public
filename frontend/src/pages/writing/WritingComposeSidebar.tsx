import React from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ChevronDown, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { GateBadge } from './writingComposeGateFlags'

const STATUS_DOT: Record<string, string> = {
  pending: 'bg-gray-300',
  generating: 'bg-amber-400 animate-pulse',
  draft: 'bg-blue-400',
  user_edited: 'bg-green-400',
  finalized: 'bg-green-600',
}

export interface WritingComposeTreeNode {
  id: number
  title: string
  depth: number
  isLeaf: boolean
  children: WritingComposeTreeNode[]
  draftStatus: string | null
  isLocked: boolean
  childDraftCount: number
  childLeafCount: number
  gateBadges: GateBadge[]
  gateWarningCount: number
  hasGeneratedDiagram: boolean
}

interface WritingComposeSidebarProps {
  projectId: string | undefined
  treeRoots: WritingComposeTreeNode[]
  currentNodeId: number | null
  collapsedNodes: Record<number, boolean>
  sidebarWidth: number
  disabled: boolean
  onSelect: (id: number) => void
  onToggle: (id: number) => void
  onDragStart: (e: React.MouseEvent) => void
}

export const WritingComposeSidebar: React.FC<WritingComposeSidebarProps> = ({
  projectId,
  treeRoots,
  currentNodeId,
  collapsedNodes,
  sidebarWidth,
  disabled,
  onSelect,
  onToggle,
  onDragStart,
}) => {
  const navigate = useNavigate()

  return (
    <div className="border-r bg-white flex-shrink-0 flex flex-col relative" style={{ width: sidebarWidth }}>
      <div className="p-3 border-b">
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => navigate(`/project/${projectId}/writing/binding`)}
        >
          <ArrowLeft className="h-4 w-4 mr-1" />
          返回
        </Button>
      </div>
      {disabled && (
        <div className="border-b bg-teal-50 px-3 py-2 text-xs text-teal-800">
          首次生成期间暂不可切换章节
        </div>
      )}
      <ScrollArea className="flex-1">
        <div className="py-1">
          {treeRoots.map((root) => (
            <SidebarTreeNode
              key={root.id}
              node={root}
              currentNodeId={currentNodeId}
              collapsedNodes={collapsedNodes}
              disabled={disabled}
              onSelect={onSelect}
              onToggle={onToggle}
            />
          ))}
        </div>
      </ScrollArea>
      <div
        className={`absolute top-0 right-0 w-1 h-full transition-colors ${
          disabled ? 'cursor-not-allowed bg-transparent' : 'cursor-col-resize hover:bg-primary/30 active:bg-primary/50'
        }`}
        onMouseDown={disabled ? undefined : onDragStart}
      />
    </div>
  )
}

interface SidebarTreeNodeProps {
  node: WritingComposeTreeNode
  currentNodeId: number | null
  collapsedNodes: Record<number, boolean>
  disabled: boolean
  onSelect: (id: number) => void
  onToggle: (id: number) => void
}

const SidebarTreeNode: React.FC<SidebarTreeNodeProps> = ({
  node, currentNodeId, collapsedNodes, disabled, onSelect, onToggle,
}) => {
  const isCollapsed = !!collapsedNodes[node.id]
  const indent = 8 + node.depth * 14

  if (node.isLeaf) {
    const dotClass = STATUS_DOT[node.draftStatus || 'pending'] || STATUS_DOT.pending
    const isActive = currentNodeId === node.id
    const primaryBadge = node.gateBadges[0]
    const badgeHint = node.gateBadges.map((b) => b.hint).join('\n')
    const hasHighSeverity = node.gateBadges.some((b) => b.severity === 'high')
    return (
      <button
        onClick={() => onSelect(node.id)}
        disabled={disabled}
        className={`w-full text-left py-1.5 rounded text-sm transition flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-60
          ${isActive ? 'bg-primary/10 text-primary font-medium' : disabled ? '' : 'hover:bg-muted'}
        `}
        style={{ paddingLeft: `${indent}px`, paddingRight: '8px' }}
        data-testid="writing-compose-leaf"
        data-gate-warning={node.gateBadges.length > 0 ? '1' : '0'}
        data-gate-severity={hasHighSeverity ? 'high' : 'normal'}
        data-has-generated-diagram={node.hasGeneratedDiagram ? '1' : '0'}
      >
        <span
          className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${dotClass}${
            node.hasGeneratedDiagram ? ' ring-2 ring-orange-500 ring-offset-1 ring-offset-white' : ''
          }`}
          title={node.hasGeneratedDiagram ? '本章含算法生成图示' : undefined}
        />
        <span className="min-w-0 flex-1 truncate">{node.title}</span>
        {primaryBadge && (
          <span
            className={`ml-auto inline-flex h-2.5 w-2.5 flex-shrink-0 rounded-full ${hasHighSeverity ? 'bg-red-500' : 'bg-amber-500'}`}
            title={badgeHint}
            data-testid="gate-badge"
            aria-label={primaryBadge.label}
          />
        )}
        {node.isLocked && <Lock className="h-3 w-3 flex-shrink-0 text-muted-foreground" />}
      </button>
    )
  }

  return (
    <div>
      <button
        onClick={() => onToggle(node.id)}
        disabled={disabled}
        className="w-full text-left py-1.5 transition flex items-center gap-1 hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
        style={{ paddingLeft: `${indent}px`, paddingRight: '8px' }}
      >
        <ChevronDown className={`h-3.5 w-3.5 flex-shrink-0 text-muted-foreground transition-transform ${isCollapsed ? '-rotate-90' : ''}`} />
        <span className={`min-w-0 flex-1 truncate ${node.depth === 0 ? 'font-semibold text-sm' : 'font-medium text-sm text-foreground/80'}`}>
          {node.title}
        </span>
        {node.gateWarningCount > 0 && (
          <span
            className="flex-shrink-0 rounded bg-red-100 px-1.5 py-0.5 text-[10px] leading-none text-red-700"
            title={`${node.gateWarningCount} 个子章节需复核`}
          >
            {node.gateWarningCount}
          </span>
        )}
        {node.childLeafCount > 0 && (
          <span className="text-xs text-muted-foreground flex-shrink-0">
            {node.childDraftCount}/{node.childLeafCount}
          </span>
        )}
      </button>
      {!isCollapsed && node.children.map((child) => (
        <SidebarTreeNode
          key={child.id}
          node={child}
          currentNodeId={currentNodeId}
          collapsedNodes={collapsedNodes}
          disabled={disabled}
          onSelect={onSelect}
          onToggle={onToggle}
        />
      ))}
    </div>
  )
}

