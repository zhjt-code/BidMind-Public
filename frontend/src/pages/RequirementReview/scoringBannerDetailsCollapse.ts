/**
 * 职责: 决定评分核对条的明细初始是否收起，以及当前是否渲染明细。
 * 不做: 不区分具体文案或业务实体，不操作 DOM，不持久化用户选择。
 */

/** 待处理时明细就是要看的内容，初始展开；其余初始收起以让出列表空间。 */
export function initialScoringDetailsCollapsed(attentionVisible: boolean): boolean {
  return !attentionVisible
}

/** 没有可折叠明细时不占展开区；有明细时跟随当前折叠态。 */
export function shouldShowScoringDetails(collapsed: boolean, hasDetails: boolean): boolean {
  return hasDetails && !collapsed
}
