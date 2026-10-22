import React from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { WritingDiagram } from '@/services/writingApi'

export interface EditableTextItem {
  id: string
  label: string
}

export interface EditableSwimNode {
  id: string
  label: string
  lane: string
}

export interface EditableLayer {
  id: string
  label: string
  itemsText: string
}

export interface EditableNetworkNode {
  id: string
  nodeId: string
  label: string
  durationDays: number
}

export interface EditableNetworkEdge {
  id: string
  source: string
  target: string
}

interface DiagramExtendedFieldsProps {
  diagramType: WritingDiagram['diagram_type']
  orgLead: string
  onOrgLeadChange: (value: string) => void
  orgUnits: EditableTextItem[]
  onOrgUnitsChange: (value: EditableTextItem[]) => void
  lanes: EditableTextItem[]
  onLanesChange: (value: EditableTextItem[]) => void
  swimNodes: EditableSwimNode[]
  onSwimNodesChange: (value: EditableSwimNode[]) => void
  layers: EditableLayer[]
  onLayersChange: (value: EditableLayer[]) => void
  networkNodes: EditableNetworkNode[]
  onNetworkNodesChange: (value: EditableNetworkNode[]) => void
  networkEdges: EditableNetworkEdge[]
  onNetworkEdgesChange: (value: EditableNetworkEdge[]) => void
  wbsRoot: string
  onWbsRootChange: (value: string) => void
  wbsPackages: EditableLayer[]
  onWbsPackagesChange: (value: EditableLayer[]) => void
  zones: EditableTextItem[]
  onZonesChange: (value: EditableTextItem[]) => void
  createItemId: (prefix: string) => string
}

const ZONE_OPTIONS = [
  '办公区', '生活区', '施工区', '材料堆场', '加工区', '大门',
  '临时道路', '围挡', '消防通道', '钢筋加工棚', '木工棚', '搅拌站',
  '仓库', '停车场', '洗车池',
]

function ListEditor({
  title,
  items,
  minCount,
  maxCount,
  onChange,
  createItemId,
  prefix,
}: {
  title: string
  items: EditableTextItem[]
  minCount: number
  maxCount: number
  onChange: (value: EditableTextItem[]) => void
  createItemId: (prefix: string) => string
  prefix: string
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>{title}</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={items.length >= maxCount}
          onClick={() => onChange([
            ...items,
            { id: createItemId(prefix), label: '' },
          ])}
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          添加
        </Button>
      </div>
      <div className="max-h-80 space-y-2 overflow-auto pr-1">
        {items.map((item, index) => (
          <div key={item.id} className="grid grid-cols-[1fr_auto] gap-2">
            <Input
              value={item.label}
              onChange={(event) => {
                const value = event.target.value
                onChange(items.map((entry, i) => (
                  i === index ? { ...entry, label: value } : entry
                )))
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={items.length <= minCount}
              onClick={() => onChange(items.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}

export const DiagramExtendedFields: React.FC<DiagramExtendedFieldsProps> = ({
  diagramType,
  orgLead,
  onOrgLeadChange,
  orgUnits,
  onOrgUnitsChange,
  lanes,
  onLanesChange,
  swimNodes,
  onSwimNodesChange,
  layers,
  onLayersChange,
  networkNodes,
  onNetworkNodesChange,
  networkEdges,
  onNetworkEdgesChange,
  wbsRoot,
  onWbsRootChange,
  wbsPackages,
  onWbsPackagesChange,
  zones,
  onZonesChange,
  createItemId,
}) => {
  if (diagramType === 'org_chart') {
    return (
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label>负责人</Label>
          <Input value={orgLead} onChange={(event) => onOrgLeadChange(event.target.value)} />
        </div>
        <ListEditor
          title="下级单元"
          items={orgUnits}
          minCount={2}
          maxCount={8}
          onChange={onOrgUnitsChange}
          createItemId={createItemId}
          prefix="unit"
        />
      </div>
    )
  }
  if (diagramType === 'swimlane') {
    return (
      <div className="space-y-3">
        <ListEditor
          title="泳道"
          items={lanes}
          minCount={2}
          maxCount={4}
          onChange={onLanesChange}
          createItemId={createItemId}
          prefix="lane"
        />
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>跨角色步骤</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={swimNodes.length >= 12}
              onClick={() => onSwimNodesChange([
                ...swimNodes,
                {
                  id: createItemId('swim'),
                  label: '',
                  lane: lanes[0]?.label || '',
                },
              ])}
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加
            </Button>
          </div>
          {swimNodes.map((node, index) => (
            <div key={node.id} className="grid grid-cols-[1fr_8rem_auto] gap-2">
              <Input
                value={node.label}
                placeholder="步骤"
                onChange={(event) => {
                  const value = event.target.value
                  onSwimNodesChange(swimNodes.map((entry, i) => (
                    i === index ? { ...entry, label: value } : entry
                  )))
                }}
              />
              <Input
                value={node.lane}
                placeholder="泳道"
                onChange={(event) => {
                  const value = event.target.value
                  onSwimNodesChange(swimNodes.map((entry, i) => (
                    i === index ? { ...entry, lane: value } : entry
                  )))
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={swimNodes.length <= 3}
                onClick={() => onSwimNodesChange(swimNodes.filter((_, i) => i !== index))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      </div>
    )
  }
  if (diagramType === 'layered') {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>分层（自上而下）</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={layers.length >= 6}
            onClick={() => onLayersChange([
              ...layers,
              { id: createItemId('layer'), label: '', itemsText: '' },
            ])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加
          </Button>
        </div>
        {layers.map((layer, index) => (
          <div key={layer.id} className="grid grid-cols-[8rem_1fr_auto] gap-2">
            <Input
              value={layer.label}
              placeholder="层名"
              onChange={(event) => {
                const value = event.target.value
                onLayersChange(layers.map((entry, i) => (
                  i === index ? { ...entry, label: value } : entry
                )))
              }}
            />
            <Input
              value={layer.itemsText}
              placeholder="组成项，逗号分隔"
              onChange={(event) => {
                const value = event.target.value
                onLayersChange(layers.map((entry, i) => (
                  i === index ? { ...entry, itemsText: value } : entry
                )))
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={layers.length <= 2}
              onClick={() => onLayersChange(layers.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
    )
  }
  if (diagramType === 'network') {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label>工序节点</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={networkNodes.length >= 12}
            onClick={() => onNetworkNodesChange([
              ...networkNodes,
              {
                id: createItemId('netn'),
                nodeId: `n${networkNodes.length + 1}`,
                label: '',
                durationDays: 7,
              },
            ])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加
          </Button>
        </div>
        {networkNodes.map((node, index) => (
          <div key={node.id} className="grid grid-cols-[5rem_1fr_5rem_auto] gap-2">
            <Input
              value={node.nodeId}
              placeholder="编号"
              onChange={(event) => {
                const value = event.target.value
                onNetworkNodesChange(networkNodes.map((entry, i) => (
                  i === index ? { ...entry, nodeId: value } : entry
                )))
              }}
            />
            <Input
              value={node.label}
              placeholder="工序"
              onChange={(event) => {
                const value = event.target.value
                onNetworkNodesChange(networkNodes.map((entry, i) => (
                  i === index ? { ...entry, label: value } : entry
                )))
              }}
            />
            <Input
              type="number"
              min={1}
              value={node.durationDays}
              onChange={(event) => {
                const value = Number(event.target.value)
                onNetworkNodesChange(networkNodes.map((entry, i) => (
                  i === index ? { ...entry, durationDays: value } : entry
                )))
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={networkNodes.length <= 3}
              onClick={() => onNetworkNodesChange(networkNodes.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <div className="flex items-center justify-between">
          <Label>紧前关系</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={networkEdges.length >= 16}
            onClick={() => onNetworkEdgesChange([
              ...networkEdges,
              {
                id: createItemId('nete'),
                source: networkNodes[0]?.nodeId || '',
                target: networkNodes[1]?.nodeId || '',
              },
            ])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加
          </Button>
        </div>
        {networkEdges.map((edge, index) => (
          <div key={edge.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <Input
              value={edge.source}
              placeholder="紧前"
              onChange={(event) => {
                const value = event.target.value
                onNetworkEdgesChange(networkEdges.map((entry, i) => (
                  i === index ? { ...entry, source: value } : entry
                )))
              }}
            />
            <Input
              value={edge.target}
              placeholder="后续"
              onChange={(event) => {
                const value = event.target.value
                onNetworkEdgesChange(networkEdges.map((entry, i) => (
                  i === index ? { ...entry, target: value } : entry
                )))
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onNetworkEdgesChange(networkEdges.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
    )
  }
  if (diagramType === 'wbs') {
    return (
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label>根节点</Label>
          <Input value={wbsRoot} onChange={(event) => onWbsRootChange(event.target.value)} />
        </div>
        <div className="flex items-center justify-between">
          <Label>工作包</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={wbsPackages.length >= 6}
            onClick={() => onWbsPackagesChange([
              ...wbsPackages,
              { id: createItemId('wbs'), label: '', itemsText: '' },
            ])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加
          </Button>
        </div>
        {wbsPackages.map((pkg, index) => (
          <div key={pkg.id} className="grid grid-cols-[8rem_1fr_auto] gap-2">
            <Input
              value={pkg.label}
              placeholder="工作包"
              onChange={(event) => {
                const value = event.target.value
                onWbsPackagesChange(wbsPackages.map((entry, i) => (
                  i === index ? { ...entry, label: value } : entry
                )))
              }}
            />
            <Input
              value={pkg.itemsText}
              placeholder="分解项，逗号分隔"
              onChange={(event) => {
                const value = event.target.value
                onWbsPackagesChange(wbsPackages.map((entry, i) => (
                  i === index ? { ...entry, itemsText: value } : entry
                )))
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={wbsPackages.length <= 2}
              onClick={() => onWbsPackagesChange(wbsPackages.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
    )
  }
  if (diagramType === 'zone_layout') {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>功能分区（示意图，非施工平面图）</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={zones.length >= 8}
            onClick={() => onZonesChange([
              ...zones,
              { id: createItemId('zone'), label: ZONE_OPTIONS[0] },
            ])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加
          </Button>
        </div>
        {zones.map((zone, index) => (
          <div key={zone.id} className="grid grid-cols-[1fr_auto] gap-2">
            <select
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={zone.label}
              onChange={(event) => {
                const value = event.target.value
                onZonesChange(zones.map((entry, i) => (
                  i === index ? { ...entry, label: value } : entry
                )))
              }}
            >
              {ZONE_OPTIONS.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={zones.length <= 3}
              onClick={() => onZonesChange(zones.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
    )
  }
  return null
}

export default DiagramExtendedFields
