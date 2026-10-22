import type { WritingBinding } from '../../services/writingApi'

export function getMatchStartErrorMessage(message: string): string {
  if (message.includes('timeout')) {
    return '启动匹配超时，请稍后重试'
  }
  return message || '触发匹配失败'
}

export function canProceedWithoutBindings(params: {
  matching: boolean
  loading: boolean
}): boolean {
  return !params.matching && !params.loading
}

export interface PendingBindingAction {
  bindingId: number | null
  action: 'confirm' | 'reject' | 'assign' | 'restore' | 'batch-confirm'
}

export function isBindingRowBusy(
  bindingId: number,
  pendingAction: PendingBindingAction | null,
): boolean {
  return pendingAction?.bindingId === bindingId
}

export function shouldShowBindingPageLoading(params: {
  initialLoaded: boolean
  loading: boolean
  hasBindings: boolean
  hasLocalAction: boolean
}): boolean {
  if (!params.initialLoaded) return true
  if (params.hasLocalAction) return false
  return params.loading && !params.hasBindings
}

export function getEmptyBindingNotice(): string {
  return '当前暂无可用绑定，可直接进入内容编辑，后续可补充素材后重新匹配'
}

export function composeNavigationState(refreshCoverage: boolean): { refreshCoverage: true } | undefined {
  return refreshCoverage ? { refreshCoverage: true } : undefined
}

export function applyRejectedBindingDecision(
  bindings: WritingBinding[],
  bindingId: number,
): WritingBinding[] {
  return bindings.map((binding) => (
    binding.id === bindingId ? { ...binding, status: 'rejected' } : binding
  ))
}

export function removeBindingById(
  bindings: WritingBinding[],
  bindingId: number,
): WritingBinding[] {
  return bindings.filter((b) => b.id !== bindingId)
}

export function isBindingNotFoundError(error: unknown): boolean {
  if (error && typeof error === 'object') {
    const statusCode = (error as { statusCode?: unknown }).statusCode
    if (statusCode != null) return statusCode === 404
  }
  if (error instanceof Error) {
    return error.message.includes('绑定不存在') || error.message.includes('绑定已更新')
  }
  return false
}

