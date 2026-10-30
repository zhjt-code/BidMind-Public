import React, { useCallback, useEffect, useState } from 'react'
import {
  DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  Check, ChevronDown, ChevronRight, CornerDownRight, GripVertical, Lock, MoreHorizontal, Pencil, Plus, Trash2, Unlock, X,
} from 'lucide-react'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { writingService, type OutlineNode } from '@/services/writingApi'

const NODE_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  fixed_template: { label: '固定模板', color: 'bg-blue-100 text-blue-800' },
  data_fill: { label: '资料填充', color: 'bg-amber-100 text-amber-800' },
  free_writing: { label: '自由论述', color: 'bg-purple-100 text-purple-800' },
}

const ACTION_BUTTON_CLASS =
  'p-1 rounded transition-opacity opacity-40 hover:opacity-100 focus-visible:opacity-100'

const SKELETON_LOCK_BUTTON_CLASS =
  'p-1 rounded border border-blue-200 bg-blue-50 text-blue-600 shadow-xs transition-colors hover:bg-blue-100 hover:text-blue-700 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500/40'

export type TreeNode = OutlineNode & { children: TreeNode[] }

export const WORD_COUNT_LIMITS: Record<string, { min: number; max: number }> = {
  data_fill: { min: 100, max: 3000 },
  free_writing: { min: 100, max: 5000 },
}

interface TreeNodeItemProps {
  node: TreeNode
  projectId: number
  onDelete: (id: number) => void
  onUpdate: () => void
  onAddChild: (parentId: number) => void
  onAddSibling: (parentId: number | null, insertAfterId: number) => void
  onReorderSiblings: (parentId: number | null, orderedIds: number[]) => void
  onEditingChange: (nodeId: number, editing: boolean) => void
  onRegisterDirtySave: (nodeId: number, saveAction: (() => Promise<boolean>) | null) => void
  onToggleUnlock: (nodeId: number) => void
  unlockedNodeIds: Set<number>
  ancestorUnlocked?: boolean
  dragHandleProps?: TreeNodeDragHandleProps
  executeWithRiskPrompt: (
    actionLabel: string,
    operation: () => Promise<void>,
  ) => Promise<void>
}

interface TreeNodeDragHandleProps {
  setActivatorNodeRef: (element: HTMLButtonElement | null) => void
  attributes: Record<string, unknown>
  listeners?: Record<string, unknown>
}

function isNodeSortable(node: TreeNode, ancestorUnlocked: boolean, unlockedNodeIds: Set<number>) {
  return node.isSkeleton !== 1 || ancestorUnlocked || unlockedNodeIds.has(node.id)
}

interface SortableOutlineSiblingGroupProps {
  parentId: number | null
  nodes: TreeNode[]
  projectId: number
  onDelete: (id: number) => void
  onUpdate: () => void
  onAddChild: (parentId: number) => void
  onAddSibling: (parentId: number | null, insertAfterId: number) => void
  onReorderSiblings: (parentId: number | null, orderedIds: number[]) => void
  onEditingChange: (nodeId: number, editing: boolean) => void
  onRegisterDirtySave: (nodeId: number, saveAction: (() => Promise<boolean>) | null) => void
  onToggleUnlock: (nodeId: number) => void
  unlockedNodeIds: Set<number>
  ancestorUnlocked: boolean
  executeWithRiskPrompt: (
    actionLabel: string,
    operation: () => Promise<void>,
  ) => Promise<void>
}

const SortableOutlineSiblingGroup: React.FC<SortableOutlineSiblingGroupProps> = ({
  parentId,
  nodes,
  projectId,
  onDelete,
  onUpdate,
  onAddChild,
  onAddSibling,
  onReorderSiblings,
  onEditingChange,
  onRegisterDirtySave,
  onToggleUnlock,
  unlockedNodeIds,
  ancestorUnlocked,
  executeWithRiskPrompt,
}) => {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const sortedNodes = [...nodes].sort((a, b) => a.sortOrder - b.sortOrder)
  const sortableIds = sortedNodes.map((node) => node.id)
  const canSortGroup = sortedNodes.length > 1
    && sortedNodes.every((node) => isNodeSortable(node, ancestorUnlocked, unlockedNodeIds))

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    if (!canSortGroup || !event.over || event.active.id === event.over.id) return
    const activeId = Number(event.active.id)
    const overId = Number(event.over.id)
    const oldIndex = sortedNodes.findIndex((node) => node.id === activeId)
    const newIndex = sortedNodes.findIndex((node) => node.id === overId)
    if (oldIndex < 0 || newIndex < 0) return
    const reordered = arrayMove(sortedNodes, oldIndex, newIndex)
    onReorderSiblings(parentId, reordered.map((node) => node.id))
  }, [canSortGroup, onReorderSiblings, parentId, sortedNodes])

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
        {sortedNodes.map((child) => (
          <SortableOutlineNode
            key={child.id}
            node={child}
            disabled={!canSortGroup}
            projectId={projectId}
            onDelete={onDelete}
            onUpdate={onUpdate}
            onAddChild={onAddChild}
            onAddSibling={onAddSibling}
            onReorderSiblings={onReorderSiblings}
            onEditingChange={onEditingChange}
            onRegisterDirtySave={onRegisterDirtySave}
            onToggleUnlock={onToggleUnlock}
            unlockedNodeIds={unlockedNodeIds}
            ancestorUnlocked={ancestorUnlocked}
            executeWithRiskPrompt={executeWithRiskPrompt}
          />
        ))}
      </SortableContext>
    </DndContext>
  )
}

interface SortableOutlineNodeProps extends Omit<TreeNodeItemProps, 'dragHandleProps'> {
  disabled: boolean
}

const SortableOutlineNode: React.FC<SortableOutlineNodeProps> = ({
  node,
  disabled,
  ...props
}) => {
  const {
    attributes,
    listeners,
    setActivatorNodeRef,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: node.id, disabled })
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.65 : undefined,
  }

  return (
    <div ref={setNodeRef} style={style}>
      <TreeNodeItem
        node={node}
        {...props}
        dragHandleProps={disabled ? undefined : {
          setActivatorNodeRef,
          attributes: attributes as unknown as Record<string, unknown>,
          listeners: listeners as Record<string, unknown> | undefined,
        }}
      />
    </div>
  )
}

interface TreeNodeActionsProps {
  node: TreeNode
  isVolumeRoot: boolean
  onStartEdit: () => void
  onAddChild: (parentId: number) => void
  onAddSibling: (parentId: number | null, insertAfterId: number) => void
  onDelete: (id: number) => void
}

const TreeNodeActions: React.FC<TreeNodeActionsProps> = ({
  node,
  isVolumeRoot,
  onStartEdit,
  onAddChild,
  onAddSibling,
  onDelete,
}) => (
  <>
    <button
      type="button"
      onClick={onStartEdit}
      className={`${ACTION_BUTTON_CLASS} text-muted-foreground hover:bg-muted`}
      aria-label={`编辑节点：${node.title}`}
      title="编辑"
    >
      <Pencil className="h-3 w-3" />
    </button>
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 p-0 text-muted-foreground opacity-60 transition-opacity hover:bg-muted hover:opacity-100 focus-visible:opacity-100"
          aria-label={`更多操作：${node.title}`}
          title="更多操作"
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
          <span className="sr-only">更多操作</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => onAddChild(node.id)}
        >
          <Plus className="h-4 w-4" />
          新增子目录
        </DropdownMenuItem>
        {!isVolumeRoot && (
          <DropdownMenuItem
            className="cursor-pointer"
            onClick={() => onAddSibling(node.parentId, node.id)}
          >
            <CornerDownRight className="h-4 w-4" />
            下方新增同级目录
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer text-destructive focus:text-destructive"
          onClick={() => onDelete(node.id)}
        >
          <Trash2 className="h-4 w-4" />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  </>
)

interface TreeNodeReadOnlyContentProps {
  node: TreeNode
  isVolumeRoot: boolean
  typeInfo: { label: string; color: string }
  canEdit: boolean
  onStartEdit: () => void
  onAddChild: (parentId: number) => void
  onAddSibling: (parentId: number | null, insertAfterId: number) => void
  onDelete: (id: number) => void
}

const TreeNodeReadOnlyContent: React.FC<TreeNodeReadOnlyContentProps> = ({
  node,
  isVolumeRoot,
  typeInfo,
  canEdit,
  onStartEdit,
  onAddChild,
  onAddSibling,
  onDelete,
}) => (
  <>
    <span className={`flex-1 text-sm ${isVolumeRoot ? 'font-semibold text-primary' : 'font-medium'}`}>
      {node.title}
    </span>
    {!isVolumeRoot && (
      <Badge className={`text-xs ${typeInfo.color}`}>{typeInfo.label}</Badge>
    )}
    {node.scoreWeight != null && (
      <span className="text-xs text-muted-foreground">{node.scoreWeight}分</span>
    )}
    {node.targetWordCount != null && (
      <span className="text-xs text-muted-foreground">建议 {node.targetWordCount} 字</span>
    )}
    {canEdit && (
      <TreeNodeActions
        node={node}
        isVolumeRoot={isVolumeRoot}
        onStartEdit={onStartEdit}
        onAddChild={onAddChild}
        onAddSibling={onAddSibling}
        onDelete={onDelete}
      />
    )}
  </>
)

export const TreeNodeItem: React.FC<TreeNodeItemProps> = ({
  node,
  projectId,
  onDelete,
  onUpdate,
  onAddChild,
  onAddSibling,
  onReorderSiblings,
  onEditingChange,
  onRegisterDirtySave,
  onToggleUnlock,
  unlockedNodeIds,
  ancestorUnlocked = false,
  dragHandleProps,
  executeWithRiskPrompt,
}) => {
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(node.title)
  const [editType, setEditType] = useState(node.nodeType)
  const [editTargetWordCount, setEditTargetWordCount] = useState(node.targetWordCount?.toString() ?? '')

  const typeInfo = NODE_TYPE_LABELS[node.nodeType] || NODE_TYPE_LABELS.free_writing
  const isSkeleton = node.isSkeleton === 1
  const isVolumeRoot = node.depth === 0 && node.volumeType != null
  const effectiveUnlocked = !isSkeleton || ancestorUnlocked || unlockedNodeIds.has(node.id)
  const allowWordCountEditing = !isVolumeRoot && editType !== 'fixed_template'
  const wordCountLimits = WORD_COUNT_LIMITS[editType] || { min: 100, max: 5000 }

  const stopEditing = useCallback(() => {
    setEditing(false)
    onEditingChange(node.id, false)
  }, [node.id, onEditingChange])

  const startEditing = useCallback(() => {
    setEditTitle(node.title)
    setEditType(node.nodeType)
    setEditTargetWordCount(node.targetWordCount?.toString() ?? '')
    setEditing(true)
    onEditingChange(node.id, true)
  }, [node.id, node.nodeType, node.targetWordCount, node.title, onEditingChange])

  const handleSave = useCallback(async (): Promise<boolean> => {
    const updates: Record<string, unknown> = {}
    const normalizedTitle = editTitle.trim()
    if (normalizedTitle !== node.title) updates.title = normalizedTitle
    if (editType !== node.nodeType) updates.node_type = editType
    if (!normalizedTitle) {
      toast.error('标题不能为空')
      return false
    }
    if (editType === 'fixed_template') {
      if (node.targetWordCount != null) updates.target_word_count = null
    } else {
      const trimmedWordCount = editTargetWordCount.trim()
      if (!trimmedWordCount) {
        if (node.targetWordCount != null) updates.target_word_count = null
      } else {
        const parsedWordCount = Number(trimmedWordCount)
        if (!Number.isInteger(parsedWordCount)) {
          toast.error('建议字数必须为整数')
          return false
        }
        if (parsedWordCount < wordCountLimits.min || parsedWordCount > wordCountLimits.max) {
          toast.error(`建议字数需在 ${wordCountLimits.min} 到 ${wordCountLimits.max} 之间`)
          return false
        }
        if (parsedWordCount !== node.targetWordCount) {
          updates.target_word_count = parsedWordCount
        }
      }
    }
    if (Object.keys(updates).length === 0) { stopEditing(); return true }
    try {
      await executeWithRiskPrompt('编辑目录节点', async () => {
        try {
          await writingService.updateNode(projectId, node.id, updates)
          toast.success('节点已更新')
          stopEditing()
          onUpdate()
        } catch {
          toast.error('更新失败')
          throw new Error('更新失败')
        }
      })
      return true
    } catch {
      return false
    }
  }, [
    editTitle,
    editType,
    editTargetWordCount,
    executeWithRiskPrompt,
    node.id,
    node.nodeType,
    node.targetWordCount,
    node.title,
    onUpdate,
    projectId,
    stopEditing,
    wordCountLimits.max,
    wordCountLimits.min,
  ])

  useEffect(() => {
    if (!editing) {
      onRegisterDirtySave(node.id, null)
      return undefined
    }
    onRegisterDirtySave(node.id, handleSave)
    return () => onRegisterDirtySave(node.id, null)
  }, [editing, handleSave, node.id, onRegisterDirtySave])

  const rowBg = isVolumeRoot
    ? 'bg-primary/5 border border-primary/20 rounded-lg mb-1'
    : 'hover:bg-muted/50'

  return (
    <div className={isVolumeRoot ? 'mb-3' : 'ml-4'}>
      <div className={`flex items-center gap-2 py-2 px-2 rounded group ${rowBg}`}>
        <div className="flex shrink-0 items-center gap-0.5">
          {dragHandleProps ? (
            <button
              type="button"
              ref={dragHandleProps.setActivatorNodeRef}
              className="p-1 rounded text-blue-500 hover:bg-blue-50 hover:text-blue-700 cursor-grab active:cursor-grabbing"
              aria-label={`拖动排序：${node.title}`}
              title="拖动调整同级顺序"
              {...dragHandleProps.attributes}
              {...dragHandleProps.listeners}
            >
              <GripVertical className="h-3.5 w-3.5" />
            </button>
          ) : (
            <span className="w-5" />
          )}

          {isSkeleton && (
            <button
              type="button"
              onClick={() => onToggleUnlock(node.id)}
              className={SKELETON_LOCK_BUTTON_CLASS}
              aria-label={`解除锁定：${node.title}`}
              title={effectiveUnlocked ? '已解除锁定' : '解除锁定后可编辑/删除'}
            >
              {effectiveUnlocked ? (
                <Unlock className="h-3.5 w-3.5 shrink-0 -scale-x-100" />
              ) : (
                <Lock className="h-3.5 w-3.5 shrink-0" />
              )}
            </button>
          )}

          {node.children.length > 0 && (
            <button onClick={() => setExpanded(!expanded)} className="p-0.5">
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          )}
        </div>

        {editing && effectiveUnlocked ? (
          <div className="flex-1 flex items-center gap-2">
            <Input
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              className="h-7 text-sm"
              onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') stopEditing() }}
              autoFocus
            />
            <Select value={editType} onValueChange={setEditType}>
              <SelectTrigger className="w-[120px] h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fixed_template">固定模板</SelectItem>
                <SelectItem value="data_fill">资料填充</SelectItem>
                <SelectItem value="free_writing">自由论述</SelectItem>
              </SelectContent>
            </Select>
            {!isVolumeRoot && (
              allowWordCountEditing ? (
                <Input
                  type="number"
                  min={String(wordCountLimits.min)}
                  max={String(wordCountLimits.max)}
                  step="100"
                  value={editTargetWordCount}
                  onChange={(e) => setEditTargetWordCount(e.target.value)}
                  className="w-[112px] h-7 text-xs"
                  placeholder="建议字数"
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') stopEditing() }}
                />
              ) : (
                <span className="text-xs text-muted-foreground whitespace-nowrap">固定模板不设字数</span>
              )
            )}
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={handleSave}>
              <Check className="h-3 w-3" />
            </Button>
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={stopEditing}>
              <X className="h-3 w-3" />
            </Button>
          </div>
        ) : (
          <TreeNodeReadOnlyContent
            node={node}
            isVolumeRoot={isVolumeRoot}
            typeInfo={typeInfo}
            canEdit={effectiveUnlocked}
            onStartEdit={startEditing}
            onAddChild={onAddChild}
            onAddSibling={onAddSibling}
            onDelete={onDelete}
          />
        )}
      </div>

      {expanded && node.children.length > 0 && (
        <div className={isVolumeRoot ? 'ml-2' : 'border-l border-muted ml-2'}>
          <SortableOutlineSiblingGroup
            parentId={node.id}
            nodes={node.children}
            projectId={projectId}
            onDelete={onDelete}
            onUpdate={onUpdate}
            onAddChild={onAddChild}
            onAddSibling={onAddSibling}
            onReorderSiblings={onReorderSiblings}
            onEditingChange={onEditingChange}
            onRegisterDirtySave={onRegisterDirtySave}
            onToggleUnlock={onToggleUnlock}
            unlockedNodeIds={unlockedNodeIds}
            ancestorUnlocked={effectiveUnlocked}
            executeWithRiskPrompt={executeWithRiskPrompt}
          />
        </div>
      )}
    </div>
  )
}
