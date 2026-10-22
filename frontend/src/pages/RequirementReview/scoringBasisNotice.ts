/**
 * 总分基准置信度提醒。
 *
 * 职责: 按基准来源分述"为什么这份总分还需你对照原文确认"。
 * 不做: 不判定通过与否、不计算分值、不承担警告态文案（`ledgerReviewHeadline` 负责）。
 *
 * 为何重写原有单一文案:
 * 1. 原文案称"未能从招标文件直接读到总分要求"——**事实错误**。doc1933(项目2447)
 * 的招标文件 page2 明写 `2.2.1 分值构成 (总分100分)`，解析期已捕获该行；
 * 只是账本桥当时仅喂 `scoring_table` chunk 正文，未读到它。断言"文件没写"
 * 会误导用户去找一个本就存在的东西。
 * （该输入缺口后由 `declaration_pages_from_signals` 读取 `scoring_signals`
 * 的 declarations/headings 两通道闭合。）
 * 2. 原文案用"推算"，而实际发生的是各评分卷分值**确定性求和**(26+30+9+30+5=100)，
 * 不含估计成分；"推算"平白削弱了结论的严谨性。
 * 3. 原单一文案覆盖了四种性质不同的基准来源，无法指导用户下一步动作。
 *
 * 为何独立成模块: `requirementReviewTransforms.ts` 已达 791 行，按复杂度护栏
 * 「存量文件禁止新增能力」不得再向其追加新函数。
 */
import type { ScoringLedgerReport } from '../requirementReviewTransforms'

const MANUAL_CHECK_NOTICE = '注意：请仔细对照招标文件核实'

/** 分值展示：整数去小数，与 formatScore 同口径（此处仅用于文案内联）。 */
function score(value: number): string {
  return String(Number(value.toFixed(4)))
}

/**
 * 取基准置信度提醒；基准已由原文交叉核对（高置信）或无基准可谈时返回 null。
 *
 * 三类低置信来源各自对应不同的用户动作：
 * - L1 各卷分值齐全但无独立总分句 → 只需确认有无遗漏卷；
 * - L2 基准由提取结果自身归集 → 属同义反复，须以原文为准复核；
 * - L3 多个互斥总分数值 → 需用户裁定适用哪一个。
 *
 * L4（无任何基准）不在此列：其 expectedTotal 为空，走警告态标题而非提醒。
 */
export function basisConfidenceNotice(
  ledger: ScoringLedgerReport | null | undefined,
): string | null {
  if (!ledger) return null
  // 叶节点逐项完整性核验已通过时，给出完整性核验文案（优先于 expectedTotal 空判断）
  if (ledger.leafIntegrity?.verified) {
    return '评分细项已通过逐项完整性核验；归卷或总分基准尚需对照原文确认，不影响细项完整性结论'
  }
  if (ledger.expectedTotal == null) return null

  if (ledger.basisLevel === 'l2_derived_group') {
    return MANUAL_CHECK_NOTICE
  }
  if (!ledger.basisLowConfidence) return null
  if (ledger.basisLevel === 'l3_doc_total') {
    return `招标文件中出现多个总分数值，已暂按 ${score(ledger.expectedTotal)} 分核对，请确认适用哪一个`
  }
  if (ledger.basisLevel === 'l1_explicit_group') {
    return MANUAL_CHECK_NOTICE
  }
  return null
}
