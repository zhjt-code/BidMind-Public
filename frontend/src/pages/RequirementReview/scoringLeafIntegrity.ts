/**
 * 职责: 解析后端 leaf_integrity 嵌套字段，提供叶节点完整性证据。
 * 不做: 不推导三态结论、不渲染 UI、不计算分值；严格 transform + 安全降级。
 *
 * 后端字段结构：
 * leaf_integrity: { verified, basis, inventory_count, matched_count, total }
 *
 * verified=true 必须同时满足：
 * - basis 为 'single_100' 或 'per_scope_100'
 * - inventory_count 为有限非负整数且 > 0
 * - matched_count 为有限非负整数且 === inventory_count
 * - total 为有限非负数（允许小数，如 99.5）且 > 0
 * 否则降级为默认未 verified。
 *
 * 诊断值保留：verified=false 降级时（非非法 basis/非法计数路径），
 * inventoryCount/matchedCount/total 保留原始值供上层诊断；仅非法输入路径返回全零。
 */

export interface ScoringLeafIntegrityEvidence {
  verified: boolean
  basis: '' | 'single_100' | 'per_scope_100'
/**
 * 清单项总数；verified=false 时仍可能包含诊断值（非零），
 * 仅在非法输入（非对象/非法 basis/非整数计数）路径下为 0。
 */
  inventoryCount: number
  /** 已匹配项数；同 inventoryCount，降级路径保留诊断值。 */
  matchedCount: number
  /** 核验基准总分（允许小数）；降级路径保留诊断值。 */
  total: number
}

const DEFAULT_EVIDENCE: ScoringLeafIntegrityEvidence = {
  verified: false,
  basis: '',
  inventoryCount: 0,
  matchedCount: 0,
  total: 0,
}

/** inventory_count / matched_count：必须为有限非负整数。 */
function toSafeNonNegativeInt(value: unknown): number | null {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value < 0
  ) return null
  return value
}

/** total：允许有限非负小数（如 99.5）。 */
function toSafeNonNegativeNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return value
}

export function transformScoringLeafIntegrity(raw: unknown): ScoringLeafIntegrityEvidence {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...DEFAULT_EVIDENCE }
  }
  const r = raw as Record<string, unknown>

  const basis = r.basis
  if (basis !== '' && basis !== 'single_100' && basis !== 'per_scope_100') {
    return { ...DEFAULT_EVIDENCE }
  }

  const ic = toSafeNonNegativeInt(r.inventory_count)
  const mc = toSafeNonNegativeInt(r.matched_count)
  const t = toSafeNonNegativeNumber(r.total)
  if (ic === null || mc === null || t === null) return { ...DEFAULT_EVIDENCE }

  const rawVerified = r.verified === true
  const verified =
    rawVerified &&
    (basis === 'single_100' || basis === 'per_scope_100') &&
    ic > 0 &&
    mc === ic &&
    t > 0

  return {
    verified,
    basis,
    inventoryCount: ic,
    matchedCount: mc,
    total: t,
  }
}
