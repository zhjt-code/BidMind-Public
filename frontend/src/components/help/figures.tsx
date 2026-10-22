import React from 'react'
import {
  ArrowLeft, ArrowRight, Check, GripVertical, HelpCircle, Lock, Plus, RefreshCw,
  Save, Play, Upload, Unlock,
} from 'lucide-react'
import type { HelpFigureId } from './types'

/**
 * 职责: 用与真实界面同款的缩略控件做帮助图示。
 * 不做: 不绑定真实操作、不读取项目数据。
 */

function Num({ n }: { n: number }) {
  return (
    <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-slate-700 text-[8px] font-bold text-white">
      {n}
    </span>
  )
}

function Chip({
  children,
  tone = 'outline',
}: {
  children: React.ReactNode
  tone?: 'outline' | 'primary' | 'ghost' | 'lock'
}) {
  const cls = {
    outline: 'border border-input bg-background text-gray-700',
    primary: 'bg-primary text-primary-foreground',
    ghost: 'text-gray-600',
    lock: 'border border-blue-200 bg-blue-50 text-blue-600',
  }[tone]
  return (
    <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>
      {children}
    </span>
  )
}

function FigureFrame({ children }: { children: React.ReactNode }) {
  return <div className="space-y-1.5">{children}</div>
}

function WritingStepBarFigure() {
  const steps = ['上传文件', '确认约束', '目录与素材', '内容编辑', '预览导出']
  return (
    <FigureFrame>
      <div className="flex items-center gap-0.5 rounded-md border bg-white px-1.5 py-1.5">
        {steps.map((label, idx) => (
          <React.Fragment key={label}>
            {idx > 0 && <span className="h-0.5 flex-1 bg-primary/30" />}
            <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-medium whitespace-nowrap ${
              idx === 2 ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary'
            }`}>
              {idx === 2 ? '3' : <Check className="h-2.5 w-2.5" />} {label}
            </span>
          </React.Fragment>
        ))}
      </div>
    </FigureFrame>
  )
}

function WritingOutlineToolbarFigure() {
  return (
    <FigureFrame>
      <div className="flex items-center justify-between rounded-md border bg-white px-1.5 py-1.5">
        <span className="flex items-center gap-1 text-[10px] text-gray-700">
          <Chip tone="ghost"><ArrowLeft className="h-2.5 w-2.5" />返回</Chip>
          <span className="font-semibold">目录编辑</span>
        </span>
        <span className="flex items-center gap-1">
          <span className="flex items-center gap-0.5">
            <Num n={1} />
            <Chip tone="outline"><Plus className="h-2.5 w-2.5" />新增目录</Chip>
          </span>
          <span className="flex items-center gap-0.5">
            <Num n={2} />
            <Chip tone="outline"><RefreshCw className="h-2.5 w-2.5" />重新生成</Chip>
          </span>
          <span className="flex items-center gap-0.5">
            <Num n={3} />
            <Chip tone="primary">下一步<ArrowRight className="h-2.5 w-2.5" /></Chip>
          </span>
        </span>
      </div>
    </FigureFrame>
  )
}

function WritingOutlineRowFigure() {
  return (
    <FigureFrame>
      <div className="rounded-md border bg-white p-1.5 space-y-1">
        <div className="flex items-center gap-1 rounded-md border border-primary/20 bg-primary/5 px-1.5 py-1">
          <span className="flex items-center gap-0.5">
            <Num n={1} />
            <GripVertical className="h-3 w-3 text-blue-500" />
          </span>
          <span className="flex items-center gap-0.5">
            <Num n={2} />
            <span className="rounded border border-blue-200 bg-blue-50 p-0.5 text-blue-600">
              <Lock className="h-2.5 w-2.5" />
            </span>
          </span>
          <span className="flex-1 text-[10px] font-medium text-gray-800">技术标</span>
          <span className="rounded bg-blue-100 px-1 py-0.5 text-[9px] text-blue-800">固定模板</span>
        </div>
        <div className="ml-4 flex items-center gap-1 rounded px-1.5 py-1">
          <GripVertical className="h-3 w-3 text-blue-500" />
          <span className="flex-1 text-[10px] text-gray-700">投标函</span>
          <span className="rounded bg-blue-100 px-1 py-0.5 text-[9px] text-blue-800">固定模板</span>
        </div>
        <div className="ml-4 flex items-center gap-1 rounded px-1.5 py-1">
          <GripVertical className="h-3 w-3 text-blue-500" />
          <span className="flex-1 text-[10px] text-gray-700">营业执照</span>
          <span className="rounded bg-amber-100 px-1 py-0.5 text-[9px] text-amber-800">资料填充</span>
        </div>
      </div>
    </FigureFrame>
  )
}

function ReviewRequirementHeaderFigure() {
  return (
    <FigureFrame>
      <div className="flex items-center justify-between rounded-md border bg-white px-1.5 py-1.5">
        <span className="text-[10px] font-semibold text-gray-800">招标要求确认</span>
        <span className="flex items-center gap-1">
          <span className="flex items-center gap-0.5">
            <Num n={1} />
            <Chip tone="outline"><Plus className="h-2.5 w-2.5" />添加</Chip>
          </span>
          <span className="flex items-center gap-0.5">
            <Num n={2} />
            <Chip tone="outline"><RefreshCw className="h-2.5 w-2.5" />重新提取</Chip>
          </span>
          <span className="flex items-center gap-0.5">
            <Num n={3} />
            <Chip tone="primary">确认并开始审查</Chip>
          </span>
        </span>
      </div>
    </FigureFrame>
  )
}

function ReviewFilterChipsFigure() {
  return (
    <FigureFrame>
      <div className="flex flex-wrap gap-1 rounded-md border bg-gray-50 px-1.5 py-1.5">
        <span className="rounded-full bg-white px-2 py-0.5 text-[9px] font-medium ring-1 ring-gray-300">全部</span>
        <span className="flex items-center gap-0.5">
          <Num n={1} />
          <span className="rounded-full bg-red-100 px-2 py-0.5 text-[9px] font-medium text-red-700">否决项</span>
        </span>
        <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[9px] font-medium text-violet-700">评分项</span>
        <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[9px] font-medium text-blue-700">技术要求</span>
      </div>
    </FigureFrame>
  )
}

function SkipVlFigure() {
  return (
    <FigureFrame>
      <div className="flex items-center gap-1.5 rounded-md border bg-white px-2 py-1.5">
        <Num n={1} />
        <span className="text-[10px] font-medium text-gray-800">图片识别</span>
        <span className="rounded bg-white px-1.5 py-0.5 text-[9px] shadow-sm ring-1 ring-gray-200">加速</span>
        <span className="rounded px-1.5 py-0.5 text-[9px] text-gray-500">完整识别</span>
        <HelpCircle className="h-3 w-3 text-gray-500" strokeWidth={2} aria-hidden="true" />
      </div>
    </FigureFrame>
  )
}

function CollusionUploadFigure() {
  return (
    <FigureFrame>
      <div className="space-y-1 rounded-md border bg-white p-1.5">
        <div className="rounded border border-dashed px-2 py-1.5 text-center text-[9px] text-gray-500">
          招标文件（建议，1 份）
        </div>
        <div className="rounded border border-dashed px-2 py-1.5 text-center text-[9px] text-gray-500">
          投标文件（2～15 份）
        </div>
        <div className="flex justify-end">
          <span className="flex items-center gap-0.5">
            <Num n={1} />
            <Chip tone="primary"><Upload className="h-2.5 w-2.5" />上传并处理</Chip>
          </span>
        </div>
      </div>
    </FigureFrame>
  )
}

function BindingFilterCardsFigure() {
  const cards: Array<{ label: string; cls: string; n: number }> = [
    { label: '高置信', cls: 'bg-green-50 text-green-700 border-green-200', n: 1 },
    { label: '低置信', cls: 'bg-amber-50 text-amber-700 border-amber-200', n: 2 },
    { label: '冲突', cls: 'bg-red-50 text-red-700 border-red-200', n: 3 },
    { label: '全局约束', cls: 'bg-slate-50 text-slate-700 border-slate-200', n: 4 },
  ]
  return (
    <FigureFrame>
      <div className="grid grid-cols-4 gap-1">
        {cards.map(c => (
          <div key={c.label} className={`rounded border px-1 py-1 text-center ${c.cls}`}>
            <div className="flex justify-center"><Num n={c.n} /></div>
            <p className="text-[9px] font-medium mt-0.5">{c.label}</p>
          </div>
        ))}
      </div>
    </FigureFrame>
  )
}

function ComposeToolbarFigure() {
  return (
    <FigureFrame>
      <div className="flex items-center justify-end gap-1 rounded-md border bg-white px-1.5 py-1.5">
        <span className="flex items-center gap-0.5">
          <Num n={1} />
          <Chip tone="lock"><Lock className="h-2.5 w-2.5" /></Chip>
        </span>
        <span className="flex items-center gap-0.5">
          <Num n={2} />
          <Chip tone="ghost"><RefreshCw className="h-2.5 w-2.5" /></Chip>
        </span>
        <Chip tone="outline"><Save className="h-2.5 w-2.5" />保存</Chip>
        <span className="flex items-center gap-0.5">
          <Num n={3} />
          <Chip tone="outline"><Play className="h-2.5 w-2.5" />重新生成全文</Chip>
        </span>
        <Chip tone="primary">预览导出</Chip>
      </div>
      <p className="flex items-center gap-1 text-[9px] text-gray-500">
        <Unlock className="h-2.5 w-2.5" />
        锁定后出现「解锁编辑」，避免被全文重生成覆盖
      </p>
    </FigureFrame>
  )
}

function HeaderUserMenuFigure() {
  const items: Array<{ n: number; label: string }> = [
    { n: 1, label: '账户中心' },
    { n: 2, label: '消息中心' },
    { n: 3, label: '修改密码' },
  ]
  return (
    <FigureFrame>
      <div className="w-[156px] overflow-hidden rounded-md border bg-white p-1 shadow-sm">
        <div className="px-1.5 py-1">
          <p className="text-[10px] font-medium leading-none text-gray-900">用户名</p>
          <p className="mt-1 text-[9px] leading-none text-gray-400">登录邮箱</p>
        </div>
        <div className="my-0.5 h-px bg-gray-100" />
        {items.map((item) => (
          <div key={item.label} className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-gray-700">
            <Num n={item.n} />
            {item.label}
          </div>
        ))}
        <div className="my-0.5 h-px bg-gray-100" />
        <div className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-gray-700">
          <Num n={4} />
          退出登录
        </div>
      </div>
    </FigureFrame>
  )
}

function KnowledgeGatesFigure() {
  const gates = ['已索引完成', '已启用', '已审核通过', '未过期']
  return (
    <FigureFrame>
      <div className="flex flex-wrap gap-1">
        {gates.map((g, i) => (
          <span key={g} className="inline-flex items-center gap-0.5 rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[9px] text-emerald-800">
            <Num n={i + 1} />
            {g}
          </span>
        ))}
      </div>
    </FigureFrame>
  )
}

const FIGURES: Record<HelpFigureId, React.FC> = {
  writing_step_bar: WritingStepBarFigure,
  writing_outline_toolbar: WritingOutlineToolbarFigure,
  writing_outline_row: WritingOutlineRowFigure,
  review_requirement_header: ReviewRequirementHeaderFigure,
  review_filter_chips: ReviewFilterChipsFigure,
  skip_vl: SkipVlFigure,
  collusion_upload: CollusionUploadFigure,
  binding_filter_cards: BindingFilterCardsFigure,
  compose_toolbar: ComposeToolbarFigure,
  knowledge_gates: KnowledgeGatesFigure,
  header_user_menu: HeaderUserMenuFigure,
}

export const HelpFigureView: React.FC<{ id: HelpFigureId; caption?: string }> = ({ id, caption }) => {
  const Fig = FIGURES[id]
  return (
    <div className="rounded-md border border-gray-200 bg-gray-50/80 p-2">
      <Fig />
      {caption ? (
        <p className="text-[10px] text-gray-500 mt-1.5 leading-relaxed">{caption}</p>
      ) : null}
    </div>
  )
}
