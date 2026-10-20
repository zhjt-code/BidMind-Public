/**
 * 职责: 集中持有三大流程各计费节点的余额门禁文案注册表 
 * 不做: 状态管理、错误解析、组件渲染
 */

/** 门禁 toast：不带预占数字，避免流程中报价 */
export const INSUFFICIENT_BALANCE_TOAST = '点数不足，请充值后继续'
/** 门禁卡片橙色提示：定性，不展示可用/需要 */
export const INSUFFICIENT_BALANCE_GATE_HINT = '当前点数不足，充值后即可继续。'

export interface BalanceGateCopy {
  /** 卡片标题（completed 语义写"上一节点完成"，retry 语义写"动作未启动"） */
  title: string
  /** 说明文案（充值后可继续什么） */
  description: string
  /** 继续按钮文案 */
  continueLabel: string
  /** 继续进行中的按钮文案 */
  busyLabel: string
}

export type BalanceGateNodeKey =
  | 'requirement_extract'
  | 'requirement_reextract'
  | 'review_start'
  | 'review_retry'
  | 'collusion_start'
  | 'collusion_retry'
  | 'writing_extract'
  | 'writing_outline'
  | 'writing_material_process'
  | 'writing_match'
  | 'writing_compose'
  | 'writing_compose_single'
  | 'writing_retry'

export const BALANCE_GATE_COPY: Record<BalanceGateNodeKey, BalanceGateCopy> = {
  requirement_extract: {
    title: '文件处理完成',
    description: '当前节点已完成，充值后可继续提取招标要求。',
    continueLabel: '继续提取',
    busyLabel: '启动中...',
  },
  requirement_reextract: {
    title: '重新提取未启动：余额不足',
    description: '当前要求清单仍保留，充值后可重新提取。',
    continueLabel: '重新提取',
    busyLabel: '启动中...',
  },
  review_start: {
    title: '招标要求已确认',
    description: '当前节点已完成，充值后可开始AI审查。',
    continueLabel: '开始审查',
    busyLabel: '启动中...',
  },
  review_retry: {
    title: '重试未启动：余额不足',
    description: '已完成的审查成果仍保留，充值后可从失败节点继续重试。',
    continueLabel: '继续重试',
    busyLabel: '重试中...',
  },
  collusion_start: {
    title: '文件处理完成',
    description: '当前节点已完成，充值后可开始串标分析。',
    continueLabel: '开始分析',
    busyLabel: '启动中...',
  },
  collusion_retry: {
    title: '重试未启动：余额不足',
    description: '已完成的分析成果仍保留，充值后可从失败节点继续重试。',
    continueLabel: '继续重试',
    busyLabel: '重试中...',
  },
  writing_extract: {
    title: '招标文件处理完成',
    description: '当前节点已完成，充值后可继续提取编写约束。',
    continueLabel: '继续提取',
    busyLabel: '启动中...',
  },
  writing_outline: {
    title: '编写约束已确认',
    description: '当前节点已完成，充值后可生成投标文件目录。',
    continueLabel: '生成目录',
    busyLabel: '启动中...',
  },
  writing_material_process: {
    title: '素材已保存',
    description: '素材文件已保存，充值后可继续解析处理。',
    continueLabel: '继续处理',
    busyLabel: '启动中...',
  },
  writing_match: {
    title: '目录已确认',
    description: '当前节点已完成，充值后可开始素材匹配。',
    continueLabel: '开始匹配',
    busyLabel: '启动中...',
  },
  writing_compose: {
    title: '素材绑定已确认',
    description: '当前节点已完成，充值后可生成标书正文。',
    continueLabel: '生成正文',
    busyLabel: '生成中...',
  },
  writing_compose_single: {
    title: '章节重新生成未启动：余额不足',
    description: '现有草稿仍保留，充值后可重新生成本章节。',
    continueLabel: '重新生成',
    busyLabel: '生成中...',
  },
  writing_retry: {
    title: '重试未启动：余额不足',
    description: '已完成的编写成果仍保留，充值后可从失败节点继续重试。',
    continueLabel: '继续重试',
    busyLabel: '重试中...',
  },
}

export function getBalanceGateCopy(nodeKey: BalanceGateNodeKey): BalanceGateCopy {
  return BALANCE_GATE_COPY[nodeKey]
}
