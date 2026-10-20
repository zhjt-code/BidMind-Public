/**
 * 招标要求列表视图 (含分组折叠 / 单分类平铺两种渲染分支)。
 *
 * 内部承担 editingId 状态 (R2-10 plan §Q5: editingId 下沉到 Table 子组件,
 * 主页面无消费方),内置 handleUpdateDescription wrapper 调用 props.onUpdateDescription
 * 并管理"提前退出编辑"时序。
 *
 * renderRequirementRow 保留为本组件内部局部 helper
 * (R2-10 plan §Q4: 不再外抽避免子组件树过深, 与 WritingMaterials/MaterialListPanel
 * 同款模式)。来源页码渲染已上提为共享组件 RequirementSourcePages,
 * 供主审查表与参考条款折叠区复用同一"来源页码→PDF 预览"成熟方案。
 */
import React, { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { getCategoryColorClasses, getCategoryLabel } from '@/lib/constants'
import type { Requirement } from '../../requirementReviewTransforms'
import { SUSPECTED_ASSESSMENT_LABEL } from '../scoringBannerCopy'
import { RequirementSourcePages } from './RequirementSourcePages'
import { ScoringItemDetail } from './ScoringItemDetail'

const isScoringDisplayItem = (req: Requirement): boolean =>
  req.requirementKind === 'scoring_item' || req.isKeyReview

const hasEditableScoreWeight = (req: Requirement): boolean => {
  const scoreWeight = Number(req.scoreWeight)
  return isScoringDisplayItem(req) && Number.isFinite(scoreWeight) && scoreWeight > 0
}

export interface RequirementTableProps {
  /** 已经 sort + filter 之后的列表 (空数组 → 渲染"无匹配要求"占位) */
  filteredList: Requirement[]
  /** 分组折叠视图所需的分组 Map (activeTab === 'all' 时使用,其余分支可传空 Map) */
  groupedMap: Map<string, Requirement[]>
  activeTab: string
  /** 已展开的分类标签集合；未出现在集合中的分类默认折叠 */
  expandedGroups: Set<string>
  onToggleGroup: (label: string) => void
  /** 项目级招标文档 ID,用于无 documentId 字段的历史 requirement 兜底 */
  tenderDocId: number | null
  /** 来源页码 chip 点击 → 打开主预览 */
  onOpenPdfPreview: (req: Requirement, page: number) => void
  /** "全部 N 页" 文字按钮 → 打开 AllSourcePagesDialog */
  onShowAllPages: (req: Requirement) => void
  /** 切换某条要求的否决项标记 */
  onToggleMandatory: (id: number) => void
  /** 提交描述更新 (主页面 hook 暴露,新旧值相同时内部 no-op) */
  onUpdateDescription: (id: number, description: string) => Promise<void>
  /** 删除某条要求 (主页面会先弹确认对话框,本组件仅触发) */
  onDelete: (id: number) => void
  /** 降为参考条款（P2.3）：移出审查打分，可在折叠区一键恢复；评分项不显示该操作 */
  onDemote: (id: number) => void
  /** 满分修正（可选）：仅评分项展示；未提供时隐藏修改入口 */
  onUpdateScoringWeight?: (id: number, weight: number) => Promise<void>
  /** 普通参考条款按分类计数（仅「全部」分组视图弱提示） */
  referenceCountsByCategory?: Record<string, number>
  /** 点击分类标题旁「参考 N」跳转到底部参考条款对应分类 */
  onJumpToReference?: (label: string) => void
}

export const RequirementTable: React.FC<RequirementTableProps> = ({
  filteredList,
  groupedMap,
  activeTab,
  expandedGroups,
  onToggleGroup,
  tenderDocId,
  onOpenPdfPreview,
  onShowAllPages,
  onToggleMandatory,
  onUpdateDescription,
  onDelete,
  onDemote,
  onUpdateScoringWeight,
  referenceCountsByCategory = {},
  onJumpToReference,
}) => {
  const [editingId, setEditingId] = useState<number | null>(null)

  // 提前 setEditingId(null) 让 UI 即时退出编辑,再 await 提交 (与原 byte-equal 时序)
  const handleUpdateDescription = async (id: number, description: string) => {
    setEditingId(null)
    await onUpdateDescription(id, description)
  }

  // 单条要求行渲染（分组与单分类视图共用）
  const renderRequirementRow = (req: Requirement, showCategory = false) => (
    <li key={req.id} className="px-4 py-4">
      <div className="grid grid-cols-12 gap-4 items-start">
        {showCategory && (
          <div className="col-span-2">
            <Badge
              variant="outline"
              className={getCategoryColorClasses(getCategoryLabel(req.category)).badge}
            >
              {getCategoryLabel(req.category)}
            </Badge>
          </div>
        )}
        <div className={showCategory ? 'col-span-6' : 'col-span-8'}>
          {editingId === req.id ? (
            <div>
              <textarea
                defaultValue={req.description}
                rows={3}
                onBlur={(e) => handleUpdateDescription(req.id, e.target.value)}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    handleUpdateDescription(req.id, e.currentTarget.value)
                  }
                  if (e.key === 'Escape') {
                    setEditingId(null)
                  }
                }}
                className="w-full border rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-primary resize-y text-sm"
                autoFocus
              />
              <p className="text-xs text-gray-400 mt-1">
                Ctrl+Enter 保存 / Esc 取消 / 点击其他位置自动保存
              </p>
            </div>
          ) : (
            <span
              className="cursor-pointer hover:text-primary text-sm leading-relaxed"
              onClick={() => setEditingId(req.id)}
              title="点击编辑"
            >
              {isScoringDisplayItem(req) && (
                <Badge
                  variant="secondary"
                  className="mr-2 align-middle text-[10px] bg-amber-100 text-amber-800 hover:bg-amber-100"
                  title="评分项：评审时重点关注并预估得分"
                >
                  评分项{req.scoreWeight != null && req.scoreWeight > 0 ? ` ${req.scoreWeight}分` : ''} · 重点
                </Badge>
              )}
              {req.scoringMetadata?.suspectedAssessmentForm && (
                <Badge
                  variant="secondary"
                  className="mr-2 align-middle text-[10px] bg-orange-50 text-orange-800 border border-orange-200 hover:bg-orange-50"
                  title={SUSPECTED_ASSESSMENT_LABEL}
                >
                  {SUSPECTED_ASSESSMENT_LABEL}
                </Badge>
              )}
              {req.description}
            </span>
          )}
          {editingId !== req.id && (
            <RequirementSourcePages
              requirement={req}
              tenderDocId={tenderDocId}
              onOpenPdfPreview={onOpenPdfPreview}
              onShowAllPages={onShowAllPages}
            />
          )}
          {editingId !== req.id && hasEditableScoreWeight(req) && (
            <ScoringItemDetail
              requirement={req}
              onUpdateScoringWeight={onUpdateScoringWeight}
            />
          )}
        </div>
        <div className="col-span-2 text-center pt-0.5">
          {/* 状态与操作分离(可发现性): 徽标纯展示, 切换由下方显式动作链接承担 */}
          <div className="flex flex-col items-center gap-1">
            <span
              className={`px-3 py-1 rounded text-sm font-medium ${
                req.isMandatory
                  ? 'bg-red-100 text-red-700'
                  : 'bg-gray-100 text-gray-500'
              }`}
            >
              {req.isMandatory ? '是' : '否'}
            </span>
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs text-gray-400 hover:text-gray-600"
              onClick={() => onToggleMandatory(req.id)}
              title={
                req.isMandatory
                  ? '取消否决项标记'
                  : '标记为否决项（不符合将直接判定审查不通过）'
              }
            >
              {req.isMandatory ? '改为否' : '改为是'}
            </Button>
          </div>
        </div>
        <div className="col-span-2 text-center pt-0.5">
          <div className="flex justify-center space-x-2">
            <Button
              variant="link"
              size="sm"
              className="text-primary text-sm h-auto px-0 py-1"
              onClick={() => setEditingId(req.id)}
            >
              编辑
            </Button>
/* 评分项铁律: 评分项/重点评审项必须参与审查, 不提供降级操作。
 色彩: indigo 为"全集/中性动作"色相, 不与编辑(蓝)/删除(红)/
 评分项徽标(amber)/分类色板(blue/cyan/amber/violet/emerald/slate)冲突 */}
            {req.requirementKind !== 'scoring_item' && !req.isKeyReview && (
              <Button
                variant="link"
                size="sm"
                className="text-indigo-600 hover:text-indigo-700 text-sm h-auto px-0 py-1"
                onClick={() => onDemote(req.id)}
                title="移出审查打分, 归入参考条款折叠区(可随时升回审查项)"
              >
                降为参考
              </Button>
            )}
            <Button
              variant="link"
              size="sm"
              className="text-destructive text-sm h-auto px-0 py-1"
              onClick={() => onDelete(req.id)}
            >
              删除
            </Button>
          </div>
        </div>
      </div>
    </li>
  )

  return (
    <>
      {/* 列表表头 */}
      <div className="px-4 py-2 border-b bg-gray-50/50">
        <div className="grid grid-cols-12 gap-4 text-xs font-semibold text-gray-700 uppercase tracking-wide">
          {activeTab === 'all' && <div className="col-span-2">分类</div>}
          <div className={activeTab === 'all' ? 'col-span-6' : 'col-span-8'}>
            要求描述
          </div>
          <div className="col-span-2 text-center">否决项</div>
          <div className="col-span-2 text-center">操作</div>
        </div>
      </div>

      {filteredList.length === 0 ? (
        <div className="p-8 text-center text-gray-400 text-sm">
          当前筛选条件下无匹配要求
        </div>
      ) : activeTab === 'all' ? (
        // 分组折叠视图
        <div>
          {Array.from(groupedMap.entries()).map(([label, items]) => {
            const isExpanded = expandedGroups.has(label)
            const mandatoryInGroup = items.filter((r) => r.isMandatory).length
            const referenceCount = referenceCountsByCategory[label] ?? 0
            return (
              <div key={label} className="border-b last:border-b-0">
                {/* 分组标题 */}
                <button
                  onClick={() => onToggleGroup(label)}
                  className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors text-left"
                >
                  <div className="flex items-center gap-2">
                    <svg
                      className={`w-4 h-4 text-gray-400 transition-transform ${
                        isExpanded ? '' : '-rotate-90'
                      }`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 9l-7 7-7-7"
                      />
                    </svg>
                    <span
                      aria-hidden
                      className={`inline-block w-2.5 h-2.5 rounded-full ${getCategoryColorClasses(label).dot}`}
                    />
                    <span className="font-medium text-gray-700 text-sm">{label}</span>
                    <span className="text-xs text-gray-400">{items.length} 条</span>
                    {mandatoryInGroup > 0 && (
                      <Badge variant="destructive" className="text-xs">
                        {mandatoryInGroup} 否决项
                      </Badge>
                    )}
                  </div>
                  {referenceCount > 0 && onJumpToReference && (
                    <span
                      role="link"
                      tabIndex={0}
                      className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                      title={`查看本类 ${referenceCount} 条参考条款`}
                      onClick={(event) => {
                        event.stopPropagation()
                        onJumpToReference(label)
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          event.stopPropagation()
                          onJumpToReference(label)
                        }
                      }}
                    >
                      参考 {referenceCount}
                      <span aria-hidden className="text-[10px] leading-none">→</span>
                    </span>
                  )}
                </button>
                {/* 分组内容 */}
                {isExpanded && (
                  <ul className="divide-y divide-gray-100">
                    {items.map((req) => renderRequirementRow(req, false))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        // 单分类平铺视图
        <ul className="divide-y divide-gray-200">
          {filteredList.map((req) => renderRequirementRow(req, false))}
        </ul>
      )}
    </>
  )
}
