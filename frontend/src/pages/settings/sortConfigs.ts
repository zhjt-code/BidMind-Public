import type { ModelConfigData } from '@/services/settingsApi'

/**
 * 模型配置卡片排序规则（按优先级自动重排）。
 *
 * 规则（三段式 + 同档稳定）：
 *   1. 在调度池中的卡片排在前面，按 priority 升序：0 → 50 → 100 → 200 → 999
 *   2. 未加入调度池的卡片排在最后
 *   3. 同档内部保持原有顺序（依赖 Array.prototype.sort 的稳定性）
 *
 * 设计取舍：
 * - 不按 model_name 字母序，避免用户刚添加的卡片突然跳到中间造成困惑
 * - 不在调度池的卡片排到最后，因为它们不参与调度，让位给在用的模型更直觉
 * - 仅做渲染层重排，不修改后端返回顺序，无需 API 改动
 */
export function sortConfigs(
  items: ModelConfigData[],
  poolIds: string[],
  poolPriority: Record<string, number>,
): ModelConfigData[] {
  const inPoolSet = new Set(poolIds)
  return [...items].sort((a, b) => {
    const aInPool = inPoolSet.has(a.id)
    const bInPool = inPoolSet.has(b.id)
    if (aInPool !== bInPool) return aInPool ? -1 : 1
    if (aInPool && bInPool) {
      const ap = poolPriority[a.id] ?? 100
      const bp = poolPriority[b.id] ?? 100
      if (ap !== bp) return ap - bp
    }
    return 0
  })
}
