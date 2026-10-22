import type { ElementType } from 'react'

/**
 * 职责: 定义帮助中心的文案结构（数据契约）。
 * 不做: 不负责渲染、不写具体指南正文。
 */

export type HelpSectionKey =
  | 'quick_start'
  | 'bid_review'
  | 'collusion'
  | 'bid_writing'
  | 'knowledge'
  | 'highlights'

export interface HelpCardItem {
  icon: ElementType
  color: string
  bgColor: string
  title: string
  description: string
  /** 有 id 则可展开，展示下列详情；无 id 时与「标书编写」等卡片同级静态展示 */
  id?: string
  summary?: string
  figures?: Array<{ id: HelpFigureId; caption: string }>
  actions?: HelpActionItem[]
  notes?: string[]
}

export interface HelpStepItem {
  icon: ElementType
  title: string
  description: string
  details?: string[]
}

export interface HelpQualityItem {
  emphasis?: boolean
  title: string
  description: string
}

/** 单个真实按钮 / 控件的说明 */
export interface HelpActionItem {
  name: string
  when: string
  how: string
  after?: string
  risk?: string
}

export type HelpFigureId =
  | 'writing_step_bar'
  | 'writing_outline_toolbar'
  | 'writing_outline_row'
  | 'review_requirement_header'
  | 'review_filter_chips'
  | 'skip_vl'
  | 'collusion_upload'
  | 'binding_filter_cards'
  | 'compose_toolbar'
  | 'knowledge_gates'
  | 'header_user_menu'

export interface HelpGuideStep {
  id: string
  title: string
  summary: string
  figures?: Array<{ id: HelpFigureId; caption: string }>
  actions?: HelpActionItem[]
  quality?: HelpQualityItem[]
  notes?: string[]
}

export type HelpBlock =
  | { kind: 'paragraph'; text: string }
  | { kind: 'callout'; tone: 'info' | 'warning' | 'success'; text: string }
  | { kind: 'cards'; items: HelpCardItem[] }
  | { kind: 'steps'; items: HelpStepItem[] }
  | { kind: 'quality'; items: HelpQualityItem[] }
  | { kind: 'notes'; items: string[] }
  | { kind: 'guideSteps'; items: HelpGuideStep[] }

export interface HelpSectionConfig {
  key: HelpSectionKey
  title: string
  icon: ElementType
  color: string
  bgColor: string
  blocks: HelpBlock[]
}
