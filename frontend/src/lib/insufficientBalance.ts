/**
 * 职责: 余额不足错误识别、结构化解析与会话级阻断快照 
 * 不做: 状态管理、UI 渲染、发起请求、文案注册
 */

export const INSUFFICIENT_BALANCE_TOAST_ID = 'insufficient-balance'
export const INSUFFICIENT_BALANCE_ERROR_CODE = 'INSUFFICIENT_BALANCE'

export interface InsufficientBalanceInfo {
  message: string
  available: number | null
  required: number | null
}

export interface BalanceGateSnapshot extends InsufficientBalanceInfo {
  projectId: number
  nodeKey: string
}

/** 旧字符串签名，兼容存量调用与测试 */
export function isInsufficientBalanceError(message: string): boolean {
  return message.includes('余额不足')
}

function parseAmount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 预占差额，仅供充值套餐挑选；required 缺失则无法提示档位 */
export function computeCreditShortfall(info: {
  available: number | null
  required: number | null
}): number | null {
  if (info.required == null || !Number.isFinite(info.required)) return null
  const available = info.available == null || !Number.isFinite(info.available) ? 0 : info.available
  const shortfall = info.required - available
  return shortfall > 0 ? shortfall : null
}

/**
 * 识别余额不足错误：结构化 errorCode 优先，旧中文文案 fallback。
 * 网络/权限/其他业务错误返回 null，交还调用方走通用错误路径。
 */
export function parseInsufficientBalanceError(error: unknown): InsufficientBalanceInfo | null {
  if (error == null) return null

  const message = typeof error === 'string'
    ? error
    : typeof (error as { message?: unknown }).message === 'string'
      ? (error as { message: string }).message
      : ''

  if (typeof error === 'object') {
    const record = error as { errorCode?: unknown; details?: unknown }
    if (record.errorCode === INSUFFICIENT_BALANCE_ERROR_CODE) {
      const details = (record.details ?? {}) as Record<string, unknown>
      return {
        message: message || '余额不足',
        available: parseAmount(details.available),
        required: parseAmount(details.required),
      }
    }
  }

  if (!message || !isInsufficientBalanceError(message)) return null
  const availableMatch = message.match(/可用[：:=\s]*(\d+)/)
  const requiredMatch = message.match(/需要[：:=\s]*(\d+)/)
  return {
    message,
    available: availableMatch ? Number(availableMatch[1]) : null,
    required: requiredMatch ? Number(requiredMatch[1]) : null,
  }
}

// 会话级阻断快照（同一浏览器标签页刷新恢复） ----------

export function balanceGateStorageKey(projectId: number, nodeKey: string): string {
  return `balance-gate:${projectId}:${nodeKey}`
}

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null
  } catch {
    return null
  }
}

export function writeBalanceGateSnapshot(snapshot: BalanceGateSnapshot, storage?: Storage): void {
  const store = resolveStorage(storage)
  if (!store) return
  try {
    store.setItem(
      balanceGateStorageKey(snapshot.projectId, snapshot.nodeKey),
      JSON.stringify(snapshot),
    )
  } catch {
    // 隐私模式等写入失败安全忽略：仅损失刷新恢复能力
  }
}

export function readBalanceGateSnapshot(
  projectId: number,
  nodeKey: string,
  storage?: Storage,
): BalanceGateSnapshot | null {
  const store = resolveStorage(storage)
  if (!store) return null
  try {
    const raw = store.getItem(balanceGateStorageKey(projectId, nodeKey))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<BalanceGateSnapshot> | null
    if (!parsed || typeof parsed !== 'object') return null
    if (parsed.projectId !== projectId || parsed.nodeKey !== nodeKey) return null
    if (typeof parsed.message !== 'string' || !parsed.message) return null
    return {
      projectId,
      nodeKey,
      message: parsed.message,
      available: parseAmount(parsed.available),
      required: parseAmount(parsed.required),
    }
  } catch {
    return null
  }
}

export function clearBalanceGateSnapshot(
  projectId: number,
  nodeKey: string,
  storage?: Storage,
): void {
  const store = resolveStorage(storage)
  if (!store) return
  try {
    store.removeItem(balanceGateStorageKey(projectId, nodeKey))
  } catch {
    // 清除失败安全忽略
  }
}
