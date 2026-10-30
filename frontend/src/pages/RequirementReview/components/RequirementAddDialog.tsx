/**
 * 手动新增招标要求/评分项的弹窗 (受控)。
 *
 * 普通要求与评分项共用一个入口，评分项字段保持结构化输入，
 * 便于后端写入 scoring_metadata 并参与满分核计。
 */
import React from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { CATEGORY_OPTIONS } from '../constants'

export interface RequirementAddDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  mode: 'requirement' | 'scoring_item'
  onModeChange: (next: 'requirement' | 'scoring_item') => void
  category: string
  description: string
  isMandatory: boolean
  scoreItemName: string
  scoreWeight: string
  scoreRules: string
  deductionRules: string
  volume: string
  volumeLabel: string
  lot: string
  sourcePages: string
  adding: boolean
  onCategoryChange: (next: string) => void
  onDescriptionChange: (next: string) => void
  onMandatoryToggle: () => void
  onScoreItemNameChange: (next: string) => void
  onScoreWeightChange: (next: string) => void
  onScoreRulesChange: (next: string) => void
  onDeductionRulesChange: (next: string) => void
  onVolumeChange: (next: string) => void
  onVolumeLabelChange: (next: string) => void
  onLotChange: (next: string) => void
  onSourcePagesChange: (next: string) => void
  onSubmit: () => void
}

export const RequirementAddDialog: React.FC<RequirementAddDialogProps> = ({
  open,
  onOpenChange,
  mode,
  onModeChange,
  category,
  description,
  isMandatory,
  scoreItemName,
  scoreWeight,
  scoreRules,
  deductionRules,
  volume,
  volumeLabel,
  lot,
  sourcePages,
  adding,
  onCategoryChange,
  onDescriptionChange,
  onMandatoryToggle,
  onScoreItemNameChange,
  onScoreWeightChange,
  onScoreRulesChange,
  onDeductionRulesChange,
  onVolumeChange,
  onVolumeLabelChange,
  onLotChange,
  onSourcePagesChange,
  onSubmit,
}) => {
  const scoreWeightValue = Number(scoreWeight)
  const submitDisabled = mode === 'scoring_item'
    ? adding || !scoreItemName.trim() || !description.trim() || !Number.isFinite(scoreWeightValue) || scoreWeightValue <= 0
    : adding || !description.trim()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{mode === 'scoring_item' ? '添加评分项' : '添加招标要求'}</DialogTitle>
          <DialogDescription>
            {mode === 'scoring_item'
              ? '补充 AI 未提取到的具体评分项，保存后会参与评分项满分核计。'
              : '选择分类并输入要求描述，手动补充 AI 未提取到的招标要求。'}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 rounded-lg bg-gray-100 p-1 text-sm">
            <button
              type="button"
              onClick={() => onModeChange('requirement')}
              className={`rounded-md px-3 py-2 font-medium ${
                mode === 'requirement'
                  ? 'bg-white text-gray-900 shadow-xs'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              普通要求
            </button>
            <button
              type="button"
              onClick={() => onModeChange('scoring_item')}
              className={`rounded-md px-3 py-2 font-medium ${
                mode === 'scoring_item'
                  ? 'bg-white text-gray-900 shadow-xs'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              评分项
            </button>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">要求分类</label>
            <select
              value={category}
              onChange={(e) => onCategoryChange(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
            >
              {CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          {mode === 'scoring_item' && (
            <div className="rounded-lg border border-amber-100 bg-amber-50/60 p-3 space-y-3">
              <p className="text-xs text-amber-800">
                只需填写必填项即可补齐评分项；卷别、页码和细分规则可在“选填信息”中补充。
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">评分项名称（必填）</label>
                <input
                  value={scoreItemName}
                  onChange={(e) => onScoreItemNameChange(e.target.value)}
                  placeholder="如：施工组织设计"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">满分分值（必填）</label>
                <input
                  type="number"
                  min={0.01}
                  step={0.5}
                  value={scoreWeight}
                  onChange={(e) => onScoreWeightChange(e.target.value)}
                  placeholder="如：20"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                />
                </div>
              </div>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {mode === 'scoring_item' ? '评分标准描述（必填）' : '要求描述'}
            </label>
            <textarea
              value={description}
              onChange={(e) => onDescriptionChange(e.target.value)}
              rows={mode === 'scoring_item' ? 3 : 4}
              placeholder={mode === 'scoring_item' ? '请输入评分项的评审内容或评分标准原文' : '请输入招标要求的详细描述'}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary resize-y"
            />
          </div>
          {mode === 'scoring_item' ? (
            <details className="rounded-lg border border-gray-200 bg-gray-50/70 p-3">
              <summary className="cursor-pointer text-sm font-medium text-gray-700">
                选填信息
                <span className="ml-2 text-xs font-normal text-gray-400">
                  评分细则、卷别、标段和来源页码
                </span>
              </summary>
              <div className="mt-3 space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">评分标准（选填）</label>
                  <textarea
                    value={scoreRules}
                    onChange={(e) => onScoreRulesChange(e.target.value)}
                    rows={3}
                    placeholder="每行一条得分/分档规则"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary resize-y"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">扣分/不得分规则（选填）</label>
                  <textarea
                    value={deductionRules}
                    onChange={(e) => onDeductionRulesChange(e.target.value)}
                    rows={3}
                    placeholder="每行一条扣分或不得分规则"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary resize-y"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">所属卷（选填）</label>
                  <select
                    value={volume}
                    onChange={(e) => onVolumeChange(e.target.value)}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                  >
                    <option value="">未归属</option>
                    <option value="technical">技术标</option>
                    <option value="commercial">商务标</option>
                    <option value="price">价格</option>
                    <option value="service">服务</option>
                    <option value="qualification">资格</option>
                    <option value="staffing">人员</option>
                    <option value="performance">业绩</option>
                    <option value="other">其他</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">卷原文标签（选填）</label>
                  <input
                    value={volumeLabel}
                    onChange={(e) => onVolumeLabelChange(e.target.value)}
                    placeholder="如：技术部分"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">采购包/标段（选填）</label>
                  <input
                    value={lot}
                    onChange={(e) => onLotChange(e.target.value)}
                    placeholder="如：采购包1"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">来源页码（选填）</label>
                  <input
                    value={sourcePages}
                    onChange={(e) => onSourcePagesChange(e.target.value)}
                    placeholder="如：12, 13"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
                  />
                </div>
              </div>
              </div>
            </details>
          ) : (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onMandatoryToggle}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                  isMandatory ? 'bg-red-500' : 'bg-gray-300'
                }`}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                    isMandatory ? 'translate-x-4' : 'translate-x-0.5'
                  }`}
                />
              </button>
              <span className="text-sm text-gray-700">否决项</span>
              {isMandatory && (
                <span className="text-xs text-red-500">不符合将直接判定不通过</span>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={onSubmit} disabled={submitDisabled}>
            {adding ? '添加中...' : '确认添加'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
