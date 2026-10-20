/**
 * 职责: 按点数差额挑选能覆盖的最小充值套餐，并标出单价最低档
 * 不做: 打开弹窗、下单、向用户展示预占数字、写死某一档为推荐
 */

export interface RechargePlanPickable {
  credits: number
  priceCents: number
  sortOrder: number
}

/** 非数字入参视为无差额；DOM onClick 仍须包一层，禁止直接绑定带参 opener */
export function normalizeRechargeMinCredits(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  return Math.ceil(value)
}

function pickDefaultPlan<T extends RechargePlanPickable>(plans: T[]): T | null {
  if (plans.length === 0) return null
  return (
    plans.find((plan) => plan.credits === 100)
    ?? [...plans].sort((a, b) => a.priceCents - b.priceCents || a.sortOrder - b.sortOrder)[0]
  )
}

export function pickBestValuePlan<T extends RechargePlanPickable>(
  plans: T[],
): T | null {
  const priced = plans.filter((plan) => plan.credits > 0 && plan.priceCents > 0)
  if (priced.length === 0) return null
  return [...priced].sort((a, b) => {
    const unitDelta = a.priceCents / a.credits - b.priceCents / b.credits
    if (unitDelta !== 0) return unitDelta
    return b.credits - a.credits
  })[0]
}

const MIN_BEST_VALUE_DISCOUNT_PERCENT = 5

export function planUnitPrice(plan: RechargePlanPickable): number | null {
  if (plan.credits <= 0 || plan.priceCents <= 0) return null
  return plan.priceCents / plan.credits
}

/** 相对上架套餐最高单价的折扣百分比；不足 5% 或无法比较时不展示。 */
export function bestValueDiscountPercent<T extends RechargePlanPickable>(
  plans: T[],
): number | null {
  const best = pickBestValuePlan(plans)
  if (best == null) return null
  const bestUnit = planUnitPrice(best)
  if (bestUnit == null) return null
  let listUnit = 0
  for (const plan of plans) {
    const unit = planUnitPrice(plan)
    if (unit != null && unit > listUnit) listUnit = unit
  }
  if (listUnit <= 0 || bestUnit >= listUnit) return null
  const percent = Math.round((1 - bestUnit / listUnit) * 100)
  if (percent < MIN_BEST_VALUE_DISCOUNT_PERCENT) return null
  return percent
}

export function pickCoveringPlan<T extends RechargePlanPickable>(
  plans: T[],
  minCredits?: number | null,
): T | null {
  if (plans.length === 0) return null
  const hint = normalizeRechargeMinCredits(minCredits)
  if (hint == null) return pickDefaultPlan(plans)

  const covering = [...plans]
    .filter((plan) => plan.credits >= hint)
    .sort((a, b) => a.credits - b.credits || a.priceCents - b.priceCents || a.sortOrder - b.sortOrder)
  if (covering.length > 0) return covering[0]

  return [...plans].sort((a, b) => b.credits - a.credits || a.priceCents - b.priceCents)[0]
}
