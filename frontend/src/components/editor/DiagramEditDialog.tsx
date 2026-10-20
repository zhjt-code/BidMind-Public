import React, { useEffect, useMemo, useState } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  writingService,
  type WritingDiagram,
  type WritingDiagramStyle,
  type WritingDiagramUpdatePayload,
} from '@/services/writingApi'
import {
  DiagramExtendedFields,
  type EditableLayer,
  type EditableNetworkEdge,
  type EditableNetworkNode,
  type EditableSwimNode,
  type EditableTextItem,
} from './DiagramExtendedFields'

interface DiagramEditDialogProps {
  open: boolean
  projectId: number
  diagramId: number
  fallbackTitle: string
  onOpenChange: (open: boolean) => void
  onUpdated: (title: string, contentSha256: string) => void | Promise<void>
}

interface EditablePhase {
  id: string
  label: string
  durationDays: number
}

interface EditableNode {
  id: string
  label: string
}

const DIAGRAM_TYPE_LABELS: Record<WritingDiagram['diagram_type'], string> = {
  flowchart: '流程图',
  gantt: '甘特图',
  swimlane: '泳道图',
  roadmap: '路线图',
  org_chart: '组织架构图',
  layered: '分层体系图',
  network: '进度网络图',
  wbs: '工作分解图',
  zone_layout: '场地分区示意图',
}
const MIN_FLOWCHART_NODES = 3
const MAX_FLOWCHART_NODES = 12
const MIN_GANTT_PHASES = 2
const MAX_GANTT_PHASES = 15
const DEFAULT_DIAGRAM_STYLE: WritingDiagramStyle = {
  theme: 'classic_blue',
  font_scale: 'md',
}
const DIAGRAM_THEME_OPTIONS: Array<{
  value: WritingDiagramStyle['theme']
  label: string
  color: string
}> = [
  { value: 'classic_blue', label: '经典蓝', color: '#4a708b' },
  { value: 'business_gray', label: '商务灰', color: '#6b7280' },
  { value: 'tech_teal', label: '科技青', color: '#14b8a6' },
]
let editableItemSequence = 0

function createEditableItemId(prefix: string): string {
  editableItemSequence += 1
  return `${prefix}-${editableItemSequence}`
}

export const DiagramEditDialog: React.FC<DiagramEditDialogProps> = ({
  open,
  projectId,
  diagramId,
  fallbackTitle,
  onOpenChange,
  onUpdated,
}) => {
  const [diagram, setDiagram] = useState<WritingDiagram | null>(null)
  const [title, setTitle] = useState(fallbackTitle)
  const [phases, setPhases] = useState<EditablePhase[]>([])
  const [nodes, setNodes] = useState<EditableNode[]>([])
  const [orgLead, setOrgLead] = useState('')
  const [orgUnits, setOrgUnits] = useState<EditableTextItem[]>([])
  const [lanes, setLanes] = useState<EditableTextItem[]>([])
  const [swimNodes, setSwimNodes] = useState<EditableSwimNode[]>([])
  const [layers, setLayers] = useState<EditableLayer[]>([])
  const [networkNodes, setNetworkNodes] = useState<EditableNetworkNode[]>([])
  const [networkEdges, setNetworkEdges] = useState<EditableNetworkEdge[]>([])
  const [wbsRoot, setWbsRoot] = useState('')
  const [wbsPackages, setWbsPackages] = useState<EditableLayer[]>([])
  const [zones, setZones] = useState<EditableTextItem[]>([])
  const [style, setStyle] = useState<WritingDiagramStyle>(DEFAULT_DIAGRAM_STYLE)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    writingService
      .getDiagram(projectId, diagramId)
      .then((data) => {
        if (!active) return
        setDiagram(data)
        setTitle(data.title || fallbackTitle)
        setPhases((data.definition.phases || []).map((phase) => ({
          id: createEditableItemId('phase'),
          label: phase.label,
          durationDays: phase.duration_days,
        })))
        setNodes((data.definition.node_labels || []).map((label) => ({
          id: createEditableItemId('node'),
          label,
        })))
        setOrgLead(data.definition.org_lead || '')
        setOrgUnits((data.definition.org_units || []).map((label) => ({
          id: createEditableItemId('unit'),
          label,
        })))
        setLanes((data.definition.lanes || []).map((label) => ({
          id: createEditableItemId('lane'),
          label,
        })))
        setSwimNodes((data.definition.swim_nodes || []).map((node) => ({
          id: createEditableItemId('swim'),
          label: node.label,
          lane: node.lane,
        })))
        setLayers((data.definition.layers || []).map((layer) => ({
          id: createEditableItemId('layer'),
          label: layer.label,
          itemsText: (layer.items || []).join('，'),
        })))
        setNetworkNodes((data.definition.network_nodes || []).map((node) => ({
          id: createEditableItemId('netn'),
          nodeId: node.node_id || '',
          label: node.label,
          durationDays: node.duration_days,
        })))
        setNetworkEdges((data.definition.network_edges || []).map((edge) => ({
          id: createEditableItemId('nete'),
          source: edge.source,
          target: edge.target,
        })))
        setWbsRoot(data.definition.wbs_root || '')
        setWbsPackages((data.definition.wbs_packages || []).map((pkg) => ({
          id: createEditableItemId('wbs'),
          label: pkg.label,
          itemsText: (pkg.items || []).join('，'),
        })))
        setZones((data.definition.zones || []).map((label) => ({
          id: createEditableItemId('zone'),
          label,
        })))
        setStyle({ ...DEFAULT_DIAGRAM_STYLE, ...(data.definition.style || {}) })
      })
      .catch((error) => {
        if (!active) return
        toast.error(error instanceof Error ? error.message : '图表信息加载失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [diagramId, fallbackTitle, open, projectId])

  const diagramTypeLabel = diagram
    ? DIAGRAM_TYPE_LABELS[diagram.diagram_type]
    : '图示'
  const canSubmit = useMemo(() => {
    if (!title.trim() || !diagram) return false
    if (diagram.diagram_type === 'gantt' || diagram.diagram_type === 'roadmap') {
      return (
        phases.length >= MIN_GANTT_PHASES &&
        phases.length <= MAX_GANTT_PHASES &&
        phases.every((phase) => phase.label.trim() && phase.durationDays > 0)
      )
    }
    if (diagram.diagram_type === 'flowchart') {
      return (
        nodes.length >= MIN_FLOWCHART_NODES &&
        nodes.length <= MAX_FLOWCHART_NODES &&
        nodes.every((node) => node.label.trim())
      )
    }
    if (diagram.diagram_type === 'org_chart') {
      return Boolean(orgLead.trim()) && orgUnits.length >= 2 && orgUnits.every((item) => item.label.trim())
    }
    if (diagram.diagram_type === 'swimlane') {
      return (
        lanes.length >= 2 &&
        swimNodes.length >= 3 &&
        lanes.every((item) => item.label.trim()) &&
        swimNodes.every((node) => node.label.trim() && node.lane.trim())
      )
    }
    if (diagram.diagram_type === 'network') {
      return (
        networkNodes.length >= 3 &&
        networkNodes.length <= 12 &&
        networkNodes.every((node) => node.nodeId.trim() && node.label.trim() && node.durationDays > 0)
      )
    }
    if (diagram.diagram_type === 'wbs') {
      return Boolean(wbsRoot.trim()) && wbsPackages.length >= 2 && wbsPackages.every((pkg) => pkg.label.trim())
    }
    if (diagram.diagram_type === 'zone_layout') {
      return zones.length >= 3 && zones.length <= 8 && zones.every((zone) => zone.label.trim())
    }
    return layers.length >= 2 && layers.every((layer) => layer.label.trim())
  }, [diagram, layers, lanes, networkEdges, networkNodes, nodes, orgLead, orgUnits, phases, swimNodes, title, wbsPackages, wbsRoot, zones])

  const handleSubmit = async () => {
    if (!diagram || !canSubmit) {
      toast.warning('请补齐图表标题和有效节点数据')
      return
    }
    const payload: WritingDiagramUpdatePayload = {
      title: title.trim(),
      style,
    }
    if (diagram.diagram_type === 'gantt' || diagram.diagram_type === 'roadmap') {
      payload.phases = phases.map((phase) => ({
        label: phase.label.trim(),
        duration_days: Number(phase.durationDays),
      }))
    } else if (diagram.diagram_type === 'flowchart') {
      payload.node_labels = nodes.map((node) => node.label.trim())
    } else if (diagram.diagram_type === 'org_chart') {
      payload.org_lead = orgLead.trim()
      payload.org_units = orgUnits.map((item) => item.label.trim())
    } else if (diagram.diagram_type === 'swimlane') {
      payload.lanes = lanes.map((item) => item.label.trim())
      payload.swim_nodes = swimNodes.map((node) => ({
        label: node.label.trim(),
        lane: node.lane.trim(),
      }))
    } else if (diagram.diagram_type === 'network') {
      payload.network_nodes = networkNodes.map((node) => ({
        node_id: node.nodeId.trim(),
        label: node.label.trim(),
        duration_days: Number(node.durationDays),
      }))
      payload.network_edges = networkEdges.map((edge) => ({
        source: edge.source.trim(),
        target: edge.target.trim(),
      }))
    } else if (diagram.diagram_type === 'wbs') {
      payload.wbs_root = wbsRoot.trim()
      payload.wbs_packages = wbsPackages.map((pkg) => ({
        label: pkg.label.trim(),
        items: pkg.itemsText.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      }))
    } else if (diagram.diagram_type === 'zone_layout') {
      payload.zones = zones.map((zone) => zone.label.trim())
    } else {
      payload.layers = layers.map((layer) => ({
        label: layer.label.trim(),
        items: layer.itemsText.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      }))
    }
    setSaving(true)
    try {
      const updated = await writingService.updateDiagram(projectId, diagramId, payload)
      await onUpdated(updated.title, updated.content_sha256)
      onOpenChange(false)
      toast.success('图表已重绘')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '图表重绘失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] flex flex-col overflow-hidden sm:max-w-2xl">
        <DialogHeader className="shrink-0">
          <DialogTitle>编辑{diagramTypeLabel}</DialogTitle>
          <DialogDescription>
            修改后会重新生成图表图片，并同步影响预览与 Word 导出。
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain pr-1">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              正在加载图表数据...
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor={`diagram-title-${diagramId}`}>图表标题</Label>
                <Input
                  id={`diagram-title-${diagramId}`}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={200}
                />
              </div>

            {diagram?.diagram_type === 'gantt' || diagram?.diagram_type === 'roadmap' ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>阶段与天数</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={phases.length >= MAX_GANTT_PHASES}
                    onClick={() => setPhases((prev) => [
                      ...prev,
                      { id: createEditableItemId('phase'), label: '', durationDays: 1 },
                    ])}
                  >
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    添加阶段
                  </Button>
                </div>
                <div className="max-h-80 space-y-2 overflow-auto pr-1">
                  {phases.map((phase, index) => (
                    <div key={phase.id} className="grid grid-cols-[1fr_7rem_auto] gap-2">
                      <Input
                        value={phase.label}
                        placeholder="阶段名称"
                        onChange={(event) => {
                          const value = event.target.value
                          setPhases((prev) => prev.map((item, i) => (
                            i === index ? { ...item, label: value } : item
                          )))
                        }}
                      />
                      <Input
                        type="number"
                        min={1}
                        value={phase.durationDays}
                        onChange={(event) => {
                          const durationDays = Number(event.target.value)
                          setPhases((prev) => prev.map((item, i) => (
                            i === index ? { ...item, durationDays } : item
                          )))
                        }}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={phases.length <= MIN_GANTT_PHASES}
                        onClick={() => setPhases((prev) => prev.filter((_, i) => i !== index))}
                        title="删除阶段"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            ) : diagram?.diagram_type === 'flowchart' ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>流程节点</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={nodes.length >= MAX_FLOWCHART_NODES}
                    onClick={() => setNodes((prev) => [
                      ...prev,
                      { id: createEditableItemId('node'), label: '' },
                    ])}
                  >
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    添加节点
                  </Button>
                </div>
                <div className="max-h-80 space-y-2 overflow-auto pr-1">
                  {nodes.map((node, index) => (
                    <div key={node.id} className="grid grid-cols-[1fr_auto] gap-2">
                      <Input
                        value={node.label}
                        placeholder="节点名称"
                        onChange={(event) => {
                          const value = event.target.value
                          setNodes((prev) => prev.map((item, i) => (
                            i === index ? { ...item, label: value } : item
                          )))
                        }}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={nodes.length <= MIN_FLOWCHART_NODES}
                        onClick={() => setNodes((prev) => prev.filter((_, i) => i !== index))}
                        title="删除节点"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            ) : diagram ? (
              <DiagramExtendedFields
                diagramType={diagram.diagram_type}
                orgLead={orgLead}
                onOrgLeadChange={setOrgLead}
                orgUnits={orgUnits}
                onOrgUnitsChange={setOrgUnits}
                lanes={lanes}
                onLanesChange={setLanes}
                swimNodes={swimNodes}
                onSwimNodesChange={setSwimNodes}
                layers={layers}
                onLayersChange={setLayers}
                networkNodes={networkNodes}
                onNetworkNodesChange={setNetworkNodes}
                networkEdges={networkEdges}
                onNetworkEdgesChange={setNetworkEdges}
                wbsRoot={wbsRoot}
                onWbsRootChange={setWbsRoot}
                wbsPackages={wbsPackages}
                onWbsPackagesChange={setWbsPackages}
                zones={zones}
                onZonesChange={setZones}
                createItemId={createEditableItemId}
              />
            ) : null}

              <div className="space-y-3 rounded-md border p-3">
                <div>
                  <Label>图件样式</Label>
                  <p className="mt-1 text-xs text-muted-foreground">
                    选择常用主题，并可按需微调主色、文字色和字号。
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>主题</Label>
                    <Select
                      value={style.theme}
                      onValueChange={(value) => setStyle((prev) => ({
                        ...prev,
                        theme: value as WritingDiagramStyle['theme'],
                      }))}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {DIAGRAM_THEME_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            <span className="inline-flex items-center gap-2">
                              <span
                                className="h-3 w-3 rounded-full"
                                style={{ backgroundColor: option.color }}
                              />
                              {option.label}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>字号</Label>
                    <Select
                      value={style.font_scale || 'md'}
                      onValueChange={(value) => setStyle((prev) => ({
                        ...prev,
                        font_scale: value as WritingDiagramStyle['font_scale'],
                      }))}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="sm">偏小</SelectItem>
                        <SelectItem value="md">标准</SelectItem>
                        <SelectItem value="lg">偏大</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`diagram-primary-color-${diagramId}`}>主色</Label>
                    <Input
                      id={`diagram-primary-color-${diagramId}`}
                      type="color"
                      value={
                        style.primary_color
                        || DIAGRAM_THEME_OPTIONS.find((item) => item.value === style.theme)?.color
                        || '#4a708b'
                      }
                      onChange={(event) => setStyle((prev) => ({
                        ...prev,
                        primary_color: event.target.value,
                      }))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`diagram-text-color-${diagramId}`}>文字色</Label>
                    <Input
                      id={`diagram-text-color-${diagramId}`}
                      type="color"
                      value={style.text_color || '#1d2b3a'}
                      onChange={(event) => setStyle((prev) => ({
                        ...prev,
                        text_color: event.target.value,
                      }))}
                    />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="shrink-0 border-t pt-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" disabled={loading || saving || !canSubmit} onClick={handleSubmit}>
            {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            重绘图表
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default DiagramEditDialog
