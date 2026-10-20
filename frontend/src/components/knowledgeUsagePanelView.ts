/**
 * 职责: 项目级知识库纳入仪器的文案、模式映射与只读可见性
 * 不做: 不发请求、不渲染、不管理知识库资料生命周期
 */

export type KnowledgeUsageFlow = 'bid_review' | 'collusion_check'

export type KnowledgeUsageMode = 'off' | 'on'

export const KNOWLEDGE_USAGE_LABEL = '知识库'

export const KNOWLEDGE_USAGE_MODE_OPTIONS: ReadonlyArray<{
  value: KnowledgeUsageMode
  label: string
}> = [
  { value: 'off', label: '关闭' },
  { value: 'on', label: '参考' },
]

export const KNOWLEDGE_USAGE_HINT: Record<KnowledgeUsageFlow, string> = {
  bid_review: '只作辅助对照，结论仍以招标原文和投标响应为准。',
  collusion_check: '用来解释正常雷同和行业模板，不会单独定串标。',
}

export const KNOWLEDGE_USAGE_PICKER_TITLE = '选择参考资料'
export const KNOWLEDGE_USAGE_EMPTY = '知识库暂无可用资料'
export const KNOWLEDGE_USAGE_SAVE = '保存'
export const KNOWLEDGE_USAGE_SAVED = '已保存'
export const KNOWLEDGE_USAGE_SAVE_FAILED = '保存失败'
export const KNOWLEDGE_USAGE_LOAD_FAILED = '知识库配置加载失败'

interface ReadOnlyKnowledgeUsagePanelState {
  loading: boolean
  enabled: boolean
  configFlow: string | null | undefined
  currentFlow: KnowledgeUsageFlow
  selectedDocumentIds: number[]
}

export function shouldRenderReadOnlyKnowledgeUsagePanel({
  loading,
  enabled,
  configFlow,
  currentFlow,
  selectedDocumentIds,
}: ReadOnlyKnowledgeUsagePanelState): boolean {
  if (loading) return true
  return enabled && configFlow === currentFlow && selectedDocumentIds.length > 0
}

export function knowledgeUsageModeFromEnabled(enabled: boolean): KnowledgeUsageMode {
  return enabled ? 'on' : 'off'
}

export function enabledFromKnowledgeUsageMode(mode: KnowledgeUsageMode): boolean {
  return mode === 'on'
}

export function formatKnowledgeUsageReadOnlySummary(selectedCount: number): string {
  return `知识库 · 已参考 ${selectedCount} 份`
}

export function formatKnowledgeUsageSelectedCount(selectedCount: number): string {
  return `已选 ${selectedCount} 份`
}

export function isKnowledgeUsageDirty(input: {
  configEnabled: boolean
  configFlow: string | null | undefined
  currentFlow: KnowledgeUsageFlow
  enabled: boolean
  selectedIds: number[]
  savedIds: number[]
}): boolean {
  const savedEnabled = Boolean(input.configEnabled && input.configFlow === input.currentFlow)
  if (input.enabled !== savedEnabled) return true
  if (!input.enabled) return false
  if (input.savedIds.length !== input.selectedIds.length) return true
  const savedSet = new Set(input.savedIds)
  return input.selectedIds.some((id) => !savedSet.has(id))
}

export function canSaveKnowledgeUsage(
  isDirty: boolean,
  enabled: boolean,
  selectedIds: number[],
): boolean {
  return isDirty && !(enabled && selectedIds.length === 0)
}
