import { FileSearch, BarChart3, Target, Brain, PenTool, BookOpen, ShieldCheck } from 'lucide-react'
import type { HelpBlock } from '../types'

export const HIGHLIGHTS_BLOCKS: HelpBlock[] = [
  {
    kind: 'paragraph',
    text: 'BidMind 把「AI 提速」和「关键节点由人把关」绑在一起：能自动的步骤自动走，会改变结论或导致废标的步骤必须人工确认。下面是使用中最值得依赖的能力。',
  },
  {
    kind: 'cards',
    items: [
      { icon: FileSearch, color: 'text-blue-600', bgColor: 'bg-blue-50/60', title: 'PDF 原文精准定位', description: '审查与分析结果中的证据可溯源到原始页面，点击页码即可对照，避免只看摘要。' },
      { icon: BarChart3, color: 'text-[#6C40A2]', bgColor: 'bg-[#F6F1FC]', title: '多维度串标分析', description: '人员、企业、文本、报价、资质、格式、异常一致、文档元数据等交叉比对，并区分招标模板造成的正常相似。' },
      { icon: Target, color: 'text-violet-600', bgColor: 'bg-violet-50/60', title: '结构化审查报告', description: '逐项展示符合 / 偏离 / 缺失，支持导出 PDF。综合符合度与预估得分仅供参考，不等于评标委员会分数。' },
      { icon: Brain, color: 'text-blue-600', bgColor: 'bg-blue-50/60', title: '智能要求与约束提取', description: '自动结构化招标要求与写标三层约束，但提取结果必须在确认页对照原文修订后，才能作为后续基准。' },
      { icon: PenTool, color: 'text-orange-600', bgColor: 'bg-orange-50/60', title: 'AI 标书智能编写', description: '按固定模板 / 资料填充 / 自由论述分流生成，再做去 AI 化与一致性检查，输出 Word。目录和类型选错会直接写偏。' },
      { icon: BookOpen, color: 'text-indigo-600', bgColor: 'bg-indigo-50/60', title: '知识库沉淀复用', description: '资料一次入库，审查辅助、串标误报抑制、写标素材引用共用；须「可用」且在当次流程中显式授权。' },
      { icon: ShieldCheck, color: 'text-emerald-600', bgColor: 'bg-emerald-50/60', title: '关键节点人工把关', description: '投标审查盯招标要求；写标盯约束、目录、绑定。这些节点漏核，后面生成得再快也不可用。' },
    ],
  },
]
