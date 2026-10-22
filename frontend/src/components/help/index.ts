import {
  Zap, Sparkles, FileText, ShieldAlert, PenTool, BookOpen,
} from 'lucide-react'
import type { HelpSectionConfig } from './types'
import { QUICK_START_BLOCKS } from './sections/quickStart'
import { BID_REVIEW_BLOCKS } from './sections/bidReview'
import { COLLUSION_BLOCKS } from './sections/collusion'
import { BID_WRITING_BLOCKS } from './sections/bidWriting'
import { KNOWLEDGE_BLOCKS } from './sections/knowledge'
import { HIGHLIGHTS_BLOCKS } from './sections/highlights'

export type {
  HelpSectionKey,
  HelpBlock,
  HelpSectionConfig,
  HelpCardItem,
  HelpStepItem,
  HelpQualityItem,
  HelpActionItem,
  HelpGuideStep,
  HelpFigureId,
} from './types'

/**
 * 职责: 汇总帮助中心各指南的区块数据，供 HelpPanel 渲染。
 * 不做: 不负责渲染或交互状态。
 */
export const HELP_SECTIONS: HelpSectionConfig[] = [
  { key: 'quick_start', title: '快速开始', icon: Zap, color: 'text-emerald-600', bgColor: 'bg-emerald-50', blocks: QUICK_START_BLOCKS },
  { key: 'bid_review', title: '投标审查指南', icon: FileText, color: 'text-blue-600', bgColor: 'bg-blue-50', blocks: BID_REVIEW_BLOCKS },
  { key: 'collusion', title: '串标审查指南', icon: ShieldAlert, color: 'text-[#6C40A2]', bgColor: 'bg-[#F6F1FC]', blocks: COLLUSION_BLOCKS },
  { key: 'bid_writing', title: '标书编写指南', icon: PenTool, color: 'text-orange-600', bgColor: 'bg-orange-50', blocks: BID_WRITING_BLOCKS },
  { key: 'knowledge', title: '知识库指南', icon: BookOpen, color: 'text-indigo-600', bgColor: 'bg-indigo-50', blocks: KNOWLEDGE_BLOCKS },
  { key: 'highlights', title: '产品亮点', icon: Sparkles, color: 'text-purple-600', bgColor: 'bg-purple-50', blocks: HIGHLIGHTS_BLOCKS },
]
