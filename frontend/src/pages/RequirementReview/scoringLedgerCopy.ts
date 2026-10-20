/**
 * 账本明细行的用户语言文案。
 *
 * 职责: 把账本的两类结构化明细（多方案信号、未归卷分值）转成不与标题重复、
 * 且不泄露内部判据的用户语言。
 * 不做: 不判定通过与否、不计算分值、不承担标题文案（`ledgerReviewHeadline` 负责）。
 *
 * 为何需要:
 * 1. 去重铁律的第五次逃逸——`insufficiency` / `low_confidence` 两类原因早已有
 * "标题已说过就不再重复"的让位门禁，而 `multi_scheme` / `unlabeled` 没有。
 * 项目2476 因此同屏出现"检测到多套评分方案，请确认本项目适用哪一套"与
 * "检测到多套评分方案：推导组和中2个≈100且Σ=200>101, 疑似多方案账本并存"，
 * 后者既重复又把 `Σ=200>101` 这类内部判据直接抛给用户。
 * 2. "另有 N 分未能归入任何评分卷"的措辞把**总分的子集**说成了增量。项目2475
 * 实测 extractedTotal=100 且 unlabeledSum=100（即 100 分全部未归组），该文案
 * 读起来是"100 分之外另有 100 分"，用户会以为系统多算了一倍。
 * 3. 组缺口与未归卷同屏矛盾: 项目2481 同时渲染"项目管理大纲部分：
 * 要求 30 分，已提取 0 分（少 30 分）"与"有 30 分尚未识别所属评分卷"——
 * 两句说的是同一批分值，分开陈述读起来像两个问题且互相否证。缺口能被未归卷
 * 分值完全解释时必须合并为单一叙事。
 *
 * 为何独立成模块: `requirementReviewTransforms.ts` 已达 860 行，按复杂度护栏
 * 「存量文件禁止新增能力」不得再向其追加新函数。
 */
import type { ScoringLedgerGroupRow } from '../requirementReviewTransforms'

/** 分值展示：整数去小数（与 formatScore 同口径，此处仅用于文案内联）。 */
function score(value: number): string {
  return String(Number(value.toFixed(4)))
}

/**
 * 多方案明细行；标题已表达"检测到多套评分方案"时返回 null（不重复）。
 *
 * 保留明细的意义是回答"依据是什么"，故仅在标题**未**以多方案为主因时渲染，
 * 且一律不回显后端原始判据串（含 `≈100`、`Σ=200>101` 等内部量）。
 */
export function multiSchemeDetail(
  signal: string,
  headlineReason: string | null,
): string | null {
  if (!signal.trim()) return null
  if (headlineReason === 'multi_scheme') return null
  return '另检测到多套评分方案的迹象，请确认本项目适用哪一套'
}

/**
 * 未归卷分值提示；展示为构成项，避免用户理解内部"归卷"诊断术语。
 */
export function unlabeledScoreNotice(
  unlabeledSum: number,
  extractedTotal: number,
): string | null {
  if (!(unlabeledSum > 0)) return null
  if (unlabeledSum >= extractedTotal - 0.01) {
    return `已提分数项 ${score(extractedTotal)} 分`
  }
  return `其它提取 ${score(unlabeledSum)} 分`
}

/** "组缺口=未归卷"合并叙事的产物：标题 + 单条明细，取代组缺口行与未归卷行。 */
export interface UnlabeledGapMerge {
  headline: string
  detail: string
}

/**
 * 组级缺口能被未归卷分值完全解释时，合并为单一"未归卷"叙事。
 *
 * 成立条件（全部满足才合并，宁可保留双行也不错并）:
 * 1. 存在未归卷分值（unlabeledSum > 0）;
 * 2. 组行只有负缺口（少提），无任何正缺口（多提意味着另有问题，不适用）;
 * 3. 负缺口合计与未归卷分值一致（±0.01）——两者是同一批分值的两种视角。
 *
 * 此时"分值与要求不一致"是误导性结论：分值都在，只是没标上所属评分卷。
 * 标题与明细都改说"未归卷"这一个事实，并点名缺口对应的评分卷供人工核对。
 */
export function mergeUnlabeledIntoGroupGaps(
  groupRows: ScoringLedgerGroupRow[],
  unlabeledSum: number,
): UnlabeledGapMerge | null {
  if (!(unlabeledSum > 0.01)) return null
  const gapRows = groupRows.filter((row) => Math.abs(row.gap) > 0.01)
  if (gapRows.length === 0) return null
  if (gapRows.some((row) => row.gap > 0)) return null
  const missingTotal = gapRows.reduce((sum, row) => sum + -row.gap, 0)
  if (Math.abs(missingTotal - unlabeledSum) > 0.01) return null
  const detail =
    gapRows.length === 1
      ? `其它提取 ${score(unlabeledSum)} 分，分值与「${gapRows[0].label}」待补的 ${score(-gapRows[0].gap)} 分一致，请核对`
      : `其它提取 ${score(unlabeledSum)} 分，与以下评分卷待补分值的合计一致：${gapRows
          .map((row) => `${row.label} ${score(-row.gap)} 分`)
          .join('、')}，请核对`
  return {
    headline: '评分项分值构成需核实',
    detail,
  }
}
