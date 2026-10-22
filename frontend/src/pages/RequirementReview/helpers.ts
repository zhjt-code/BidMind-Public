/**
 * 招标要求确认页（RequirementReview）的纯函数 helper。
 *
 * 单独抽出为 .ts 文件以便 `frontend/tests/` 直接 import 做契约测试，
 * 主 `index.tsx` 仅做编排，禁止在主文件内重复实现这些函数。
 *
 * 关键不变量：
 * - sortRequirements 必须先按"是否否决项"排序，再按分类优先级排序
 * - mapExtractionToSteps / mapStandardExtractionSteps 必须返回索引顺序
 * 与 EXTRACTION_STEPS 一致的 ProgressStep[]
 * - mapStandardExtractionSteps 在 rawSteps 解析后无步骤时返回 null
 * （触发主页面回退到 mapExtractionToSteps 兜底路径）
 */

import { getCategoryLabel } from '../../lib/constants.ts'
import type { ProgressStep } from '../../hooks/useProgressAnimation.ts'
import {
  mapProgressStatusSteps,
  normalizeStepStatus,
} from '../../services/progressStatusTransforms.ts'
import type { Requirement } from '../requirementReviewTransforms.ts'
import {
  EXTRACTION_STAGE_RANGES,
  EXTRACTION_STEPS,
  categoryOrder,
} from './constants.ts'

/** 取分类对应的排序权重，未识别的分类用 99 兜底排到最后。 */
export const getCategoryOrder = (category: string): number => {
  return categoryOrder[category] || categoryOrder[category.toUpperCase()] || 99
}

/**
 * 招标要求列表排序：
 * 第一层：否决项优先（true 在前）
 * 第二层：按分类优先级 ascending
 *
 * 输入数组不被原地修改，遵循 frontend.mdc"排序前先浅拷贝"规则。
 */
export const sortRequirements = (requirements: Requirement[]): Requirement[] => {
  return [...requirements].sort((a, b) => {
    if (a.isMandatory !== b.isMandatory) {
      return a.isMandatory ? -1 : 1
    }
    return getCategoryOrder(a.category) - getCategoryOrder(b.category)
  })
}

/** 一个分类聚合的展示分组（label 为分类中文标签，items 为该类全部要求）。 */
export interface RequirementGroup {
  label: string
  items: Requirement[]
}

/**
 * 折叠区风险分组拆分（P1.4）：
 * - riskItems: isMandatory=true 的折叠项（行为性否决条款经双确认降级而来），
 * 必须以独立"风险告知"分组置顶红色展示，不得与普通参考条款混排（红线）
 * - referenceItems: 其余普通参考条款，仍走分类聚合展示
 *
 * 输入数组不被原地修改。
 */
export const splitFoldedRequirements = (
  items: Requirement[],
): { riskItems: Requirement[]; referenceItems: Requirement[] } => {
  const riskItems: Requirement[] = []
  const referenceItems: Requirement[] = []
  for (const req of items) {
    if (req.isMandatory) {
      riskItems.push(req)
    } else {
      referenceItems.push(req)
    }
  }
  riskItems.sort((a, b) => a.id - b.id)
  return { riskItems, referenceItems }
}

/**
 * 按分类聚合招标要求，用于参考条款折叠区的"同类聚合 + 分类排序"展示。
 *
 * 排序契约：
 * - 分组顺序按分类优先级 ascending（QUAL>TECH>COMM>PERF>STAFF>FORMAT，未知 99 垫底）
 * - 组内按 id ascending（稳定可预期，避免后端返回顺序抖动导致展示漂移）
 * - 枚举 name 与 value（QUAL / qualification）经 getCategoryLabel 归一后并入同一组
 *
 * 输入数组不被原地修改，遵循 frontend.mdc"排序前先浅拷贝"规则。
 */
export const groupRequirementsByCategory = (
  requirements: Requirement[],
): RequirementGroup[] => {
  const sorted = [...requirements].sort((a, b) => {
    const orderDiff = getCategoryOrder(a.category) - getCategoryOrder(b.category)
    if (orderDiff !== 0) return orderDiff
    return a.id - b.id
  })
  const groups: RequirementGroup[] = []
  const indexByLabel = new Map<string, number>()
  for (const req of sorted) {
    const label = getCategoryLabel(req.category)
    let idx = indexByLabel.get(label)
    if (idx === undefined) {
      idx = groups.length
      indexByLabel.set(label, idx)
      groups.push({ label, items: [] })
    }
    groups[idx].items.push(req)
  }
  return groups
}

/**
 * 普通参考条款按分类标签计数（供主表「参考 N」弱提示）。
 *
 * 调用方须先传入已剔除风险项的 referenceItems；本函数不做 isMandatory 过滤。
 * 分类键经 getCategoryLabel 归一（QUAL / qualification → 同一标签）。
 */
export const countReferenceByCategory = (
  requirements: Requirement[],
): Record<string, number> => {
  const counts: Record<string, number> = {}
  for (const req of requirements) {
    const label = getCategoryLabel(req.category)
    counts[label] = (counts[label] ?? 0) + 1
  }
  return counts
}

/**
 * 评分核对范围判据：与 RequirementTable 行内徽标同源。
 *
 * 含两类：分值明确的评分项（requirementKind === 'scoring_item'）与
 * 无具体分值但需重点评审的项（isKeyReview），两者都属于用户要核对的评分范围。
 */
export const isScoringFocusItem = (req: Requirement): boolean =>
  req.requirementKind === 'scoring_item' || req.isKeyReview

/** 评分核对范围聚合结果（展示层口径，不参与后端分值校验）。 */
export interface ScoringFocusSummary {
  /** 评分核对范围总项数（含无具体分值的重点项）。 */
  totalCount: number
  /** 其中带有效正分值的项数。 */
  scoredCount: number
  /** 其中无有效分值、仅作重点评审的项数。 */
  keyOnlyCount: number
  /** 带有效分值项的分值合计（两位小数收敛，规避浮点尾差）。 */
  totalScore: number
}

/**
 * 聚合评分核对范围：项数 + 分值合计 + 无分值重点项数。
 *
 * 分值口径：仅 scoreWeight 为有限正数时计入 scoredCount 与 totalScore；
 * null / NaN / <= 0 一律归入 keyOnlyCount，禁止用 0 冒充分值造成"合计虚低但项数对得上"的误导。
 * 本函数只做展示层聚合，不与后端 scoring_totals_report 的权威校验结论竞争。
 */
export const buildScoringFocusSummary = (
  requirements: Requirement[],
): ScoringFocusSummary => {
  let totalCount = 0
  let scoredCount = 0
  let keyOnlyCount = 0
  let totalScore = 0
  for (const req of requirements) {
    if (!isScoringFocusItem(req)) continue
    totalCount += 1
    const weight = Number(req.scoreWeight)
    if (Number.isFinite(weight) && weight > 0) {
      scoredCount += 1
      totalScore += weight
    } else {
      keyOnlyCount += 1
    }
  }
  return {
    totalCount,
    scoredCount,
    keyOnlyCount,
    totalScore: Number(totalScore.toFixed(2)),
  }
}

/**
 * 取含评分核对项的分类标签集合（供一键聚焦时展开对应折叠分组）。
 *
 * 标签经 getCategoryLabel 归一（QUAL / qualification → 同一标签）并去重，
 * 顺序按分类优先级 ascending，与主列表分组顺序一致。
 */
export const getScoringFocusGroupLabels = (
  requirements: Requirement[],
): string[] => {
  const focusItems = requirements.filter(isScoringFocusItem)
  return groupRequirementsByCategory(focusItems).map((group) => group.label)
}

/**
 * 确认审查前风险告知摘要（仅展示层闸门，不改变审查启动语义）。
 */
export const buildRiskNoticeSummary = (
  riskItems: Requirement[],
): { total: number; hasRisk: boolean } => ({
  total: riskItems.length,
  hasRisk: riskItems.length > 0,
})

/**
 * 按 stage / 整体进度 / 描述消息生成 ProgressStep[] + overall。
 *
 * 用于后端只返回 stage 字符串而无 steps 数组时的兜底路径：
 * - 当前 stage 之前的步骤标记 completed
 * - 当前 stage 按进度区间换算为 processing 子进度
 * - 之后的步骤保持 pending
 * - stage === 'completed' (idx 越界) 时全部 completed
 */
export function mapExtractionToSteps(
  stage: string,
  progress: number,
  message: string,
): { steps: ProgressStep[]; overall: number } {
  const info = EXTRACTION_STAGE_RANGES[stage]
  const currentIdx = info?.idx ?? -1

  const steps: ProgressStep[] = EXTRACTION_STEPS.map((base, i) => {
    if (currentIdx >= EXTRACTION_STEPS.length) {
      return { ...base, status: 'completed' as const, targetProgress: 1 }
    }
    if (i < currentIdx) {
      return { ...base, status: 'completed' as const, targetProgress: 1 }
    }
    if (i === currentIdx && info) {
      const [start, end] = info.range
      const stepProg = end > start
        ? Math.max(0, Math.min(1, (progress - start) / (end - start)))
        : 1
      return { ...base, status: 'processing' as const, targetProgress: stepProg, detail: message }
    }
    return { ...base, status: 'pending' as const, targetProgress: 0 }
  })

  return { steps, overall: Math.min(1, progress / 100) }
}

/**
 * 后端返回标准 steps[] 数组时优先消费：
 * - 用 mapProgressStatusSteps + normalizeStepStatus 转换为前端 ProgressStep
 * - 步骤 id 优先取后端下发的 step.key（与 EXTRACTION_STEPS 同名），
 * 后端未下发 key 时回退到索引对位，再回退 `extract-{i}`
 * - 当 steps[] 为空（已规范化为长度 0）时返回 null，由主页面回退到 mapExtractionToSteps
 * - stageText 优先用 fallbackText，否则从 processing 步骤推断
 */
export function mapStandardExtractionSteps(
  rawSteps: unknown,
  overallProgress: number,
  fallbackText: string,
): { steps: ProgressStep[]; overall: number; stageText: string } | null {
  const apiSteps = mapProgressStatusSteps(rawSteps)
  if (apiSteps.length === 0) return null

  const steps: ProgressStep[] = apiSteps.map((step, index) => ({
    id: step.key || EXTRACTION_STEPS[index]?.id || `extract-${index}`,
    name: step.label,
    status: normalizeStepStatus(step.status),
    progress: 0,
    targetProgress: Math.max(0, Math.min(step.progress / 100, 1)),
    detail: step.detail,
  }))

  const runningStep = steps.find((step) => step.status === 'processing')
  let stageText = fallbackText
  if (!stageText && runningStep) {
    const idx = steps.indexOf(runningStep) + 1
    stageText = `步骤${idx} ${runningStep.name}`
    if (runningStep.detail) stageText += ` · ${runningStep.detail}`
  }

  return {
    steps,
    overall: Math.max(0, Math.min(overallProgress / 100, 1)),
    stageText,
  }
}
