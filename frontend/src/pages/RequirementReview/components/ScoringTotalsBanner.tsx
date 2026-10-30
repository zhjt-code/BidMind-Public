/**
 * 评分项核对提示条。
 *
 * 信息架构（统一梳理，新增内容必须归入其中一层，禁止旁开新块）:
 * 1. 主结论(必有, 一行): 事实(提取项数+分值构成) + 判定(是否通过校验)。
 * 绿色 check 图标=通过 / 琥珀 alert 图标=需处理; 图标形状必须随语义切换。
 * 2. 问题明细(仅需处理时): 具体哪里不对 + 怎么办。
 * 3. 提醒(蓝色, 仅"需确认但非错误"): 结论自洽、只存在信息缺口。区块内不再放
 * 圆形图标——主结论图标已表达整体状态, 重复圆形图标是视觉噪音。
 * 4. 详情折叠(仅 report.diagnostics): 含页码定位, 可核对原文。
 * 5. 行动区(末位, 右对齐): 两个动作按钮(聚焦定位 / 手工补录)。不再重复主结论
 * 已包含的评分项数量与分值范围。折叠时第 1 层与第 5 层仍保留；第 2–4 层
 * 收入明细。待处理时明细初始展开，其余初始收起。
 *
 * 去重铁律: 任一层不得复述其他层已给出的结论。历史上"主结论"与"账本通过态"
 * 曾同时陈述"提取100分/校验通过", 账本通过态已删除——它是 isOk 的
 * 子条件, 天然被主结论覆盖。
 *
 * 同一铁律下的第二处让位: legacy 报告(report)与账本报告(ledgerReport)是同一批
 * 评分项的两条轴(枚举卷 vs 原文组标签, 见 ADR 2026-07-28 换轴)。账本区块渲染时
 * 已自带判定标题 + 总分对比 + 逐卷差额, legacy 的"分值不一致, 请人工核对"与
 * "请对照评分表补齐或修正"退化为同义泛化句, 一律不渲染; 仅当两轴真的各自检出
 * 不同问题(账本无任何差额行而 legacy 有卷缺口)时才保留 legacy 明细, 避免静默丢信息。
 *
 * 不展示: 逻辑表编号/内部错误码/基准分级枚举/账本 diagnostics 等实现细节
 * (: 对用户零信息量; 排障走后端日志与账本报告接口)。
 */
import React from 'react'
import { Button } from '@/components/ui/button'
import {
  buildScoringTotalsSummary,
  formatLedgerGroupRow,
  formatScore,
  isLedgerGapFoldingArtifact,
  isLedgerSelfConsistentWithSoftBasis,
  ledgerReviewHeadline,
  ledgerTotalsLine,
  resolveLedgerReviewReason,
  volumeLabel,
  type ScoringLedgerReport,
  type ScoringTotalsReport,
} from '../../requirementReviewTransforms'
import { basisConfidenceNotice } from '../scoringBasisNotice'
import {
  buildScoringBannerHeadlineText,
  buildScoringCompositionText,
  buildSuspectedAssessmentNotice,
  SCORING_MANUAL_CHECK_NOTICE,
} from '../scoringBannerCopy'
import { ledgerGroupShares } from '../scoringGroupShares'
import {
  mergeUnlabeledIntoGroupGaps,
  multiSchemeDetail,
} from '../scoringLedgerCopy'
import type { ScoringFocusSummary } from '../helpers'
import {
  buildScoringAddItemVisibilityInput,
  shouldShowAddScoringItem,
} from '../scoringAddItemVisibility'
import { buildScopeReportView } from '../scoringScopeReportTransforms'
import type { ScoringVerdict } from '../scoringVerdict'
import { ScopedScoringReportPanel } from './ScopedScoringReportPanel'
import { ScoringBannerChrome } from './ScoringBannerChrome'

export interface ScoringTotalsBannerProps {
  report: ScoringTotalsReport | null
  /** 评分账本对账报告(P5-1)；冻结窗口内与 legacy 报告并存展示,独立小节渲染。 */
  ledgerReport?: ScoringLedgerReport | null
/**
 * 当前主审查列表内的评分核对范围聚合(展示层口径, 来自 buildScoringFocusSummary)。
 * 仅用于聚焦按钮的可用性判定, 不参与任何校验结论。
 */
  focusSummary?: ScoringFocusSummary | null
/**
 * 评分三态权威结论（verdict 单源）：非 null 时直接决定 green/blue/amber
 * 三态与通过判定，本地公式仅在 null/缺省时回退（行为与历史版本完全一致）。
 */
  verdict?: ScoringVerdict | null
  onAddScoringItem?: () => void
  /** 一键聚焦: 把主列表筛选为评分项/重点项并滚动定位(由页面侧实现)。 */
  onFocusScoringItems?: () => void
}

/** 单卷差异的用户语言描述: 缺/多 X 分, 不暴露归因术语。 */
function volumeGapText(vol: string, sum: number, expected: number): string {
  const delta = Number((expected - sum).toFixed(4))
  const direction = delta > 0 ? `缺 ${formatScore(delta)} 分` : `多 ${formatScore(-delta)} 分`
  return `${volumeLabel(vol)}：${direction}（已识别 ${formatScore(sum)} 分 / 应为 ${formatScore(expected)} 分）`
}

export const ScoringTotalsBanner: React.FC<ScoringTotalsBannerProps> = ({
  report,
  ledgerReport,
  focusSummary,
  verdict,
  onAddScoringItem,
  onFocusScoringItems,
}) => {
  const summary = report ? buildScoringTotalsSummary(report) : null
  // 折算制下"账本基准≡折算基准、差额恰等于未折算量"是纯假差额。首行已给出
  // "折算基准100分"，再报"比要求多100分"与之自相矛盾；此类差额不构成需复核。
  const ledgerGapIsFoldingArtifact = isLedgerGapFoldingArtifact(
    summary?.folded ?? null,
    ledgerReport ?? null,
  )
  // 分值已闭合、唯一需复核原因是基准未经原文交叉核对时，系统内部是自洽的,
  // 不存在需修正的错误, 只剩一处需用户对照原文确认的信息缺口——按提醒而非警告呈现。
  const ledgerSoftBasisOnly = isLedgerSelfConsistentWithSoftBasis(ledgerReport ?? null, report)
  // P5-1 账本对账: 仅 reconciled 且基准非低置信才算通过；
  // 低置信基准(推导/默认百分制假设)禁止渲染绿色通过态文案，一律走复核小节。
  const ledgerReconciledOk =
    !!ledgerReport && ledgerReport.status === 'reconciled' && !ledgerReport.basisLowConfidence
  const ledgerNeedsReview =
    !!ledgerReport &&
    !ledgerReconciledOk &&
    !ledgerGapIsFoldingArtifact &&
    !ledgerSoftBasisOnly
  // Task7: 多包模式下全局对比被抑制。
  // globalComparisonAllowed=false 时 effectiveLedgerNeedsReview 为 false，
  // 确保 top-level L4 基准不将多scope全通过的横幅拉回 amber。
  const globalComparisonAllowed = ledgerReport?.globalComparisonAllowed !== false
  const scopeView =
    !globalComparisonAllowed && ledgerReport
      ? buildScopeReportView(ledgerReport.scopeReports)
      : null
  // 三态驱动：
  // scopeAmber = 存在有真实缺口的 applicable scope → amber
  // isScopeInfoOnly = 无真实缺口但存在 unresolved/low-confidence scope → blue 中性
  // 两者均为 false 且无其他错误 → green
  const scopeAmber = !!scopeView && (scopeView.anyApplicableGap || scopeView.anyMisclassifiedGap)
  const isScopeInfoOnly = !scopeAmber && !!scopeView && scopeView.anyUnresolved
  const effectiveLedgerNeedsReview = globalComparisonAllowed ? ledgerNeedsReview : false
  // P5-1 账本报告存在时必须展示，不因旧报告为空而隐藏整个 Banner。
  if (!report && !ledgerReport) return null

  const visibleVolumes = report ? Object.entries(report.volumeChecks) : []
  const problemVolumes = visibleVolumes.filter(([, check]) => !check.within)
  const reportNeedsReview = !!report && report.needsReview
  const legacyGroupShares = ledgerGroupShares(ledgerReport)
  // 并行百分制卷(100/100/100)无文档总分时，账本只能给L2低置信自洽；
  // 若分值/规则本身无缺口，旧轴固定套100产生的“多200分”应让位，但蓝色基准提醒保留。
  // missingRule/missingScore 任一存在都不得让位，避免用总分自洽掩盖真实条目缺陷。
  const legacySoftBasisSuperseded =
    !!report &&
    ledgerSoftBasisOnly &&
    report.missingRuleCount === 0 &&
    report.missingScoreCount === 0 &&
    problemVolumes.length === 0
  // 账本轴已自洽且原文组行完整覆盖提取总分时，legacy 旧轴复核结论整体让位。
  // 2471 重提取后账本三卷 20/20/60 全闭合，仅因 legacy missingScoreCount=1
  // 被拉回 amber，并同屏渲染"分值不一致"与"三卷合计100"的矛盾文案。
  // Task7: scoped reports 接管时（scopeView 存在）legacy 同样整体让位；
  // 此分支独立于 !ledgerNeedsReview，不受 L4 基准的 raw ledgerNeedsReview 阻断。
  // 两个 OR 分支均有显式括号，语义一目了然。
  const legacyReviewSuperseded =
    !report?.structureReviewRequired &&
    ((reportNeedsReview && !ledgerNeedsReview && (legacyGroupShares.length > 0 || legacySoftBasisSuperseded)) ||
      (reportNeedsReview && !!scopeView))
  const effectiveReportNeedsReview = reportNeedsReview && !legacyReviewSuperseded
  // verdict 单源：后端 scoring_verdict 非空时是通过判定的唯一权威（与后端 VLM
  // 救援触发消费同一结论），本地公式仅在 verdict 缺失时回退。
  const isOk =
    verdict != null ? verdict === 'green' : !effectiveReportNeedsReview && !effectiveLedgerNeedsReview && !scopeAmber && !isScopeInfoOnly
  // Task7 C-NEW-1: blue 态的严格门控——仅当 isScopeInfoOnly 且无任何其他真实错误时才 blue。
  // legacy 错误、scope gap、账本复核 任一存在时，不得降级为 blue 中性，必须 amber。
  const hasOtherRealError =
    effectiveReportNeedsReview || effectiveLedgerNeedsReview || scopeAmber
  const scoreGapNeedsReview =
    effectiveReportNeedsReview && (report!.missingScoreCount > 0 || problemVolumes.length > 0)
  const missingRuleHint =
    report && report.missingRuleCount > 0
      ? `有 ${report.missingRuleCount} 项评分项评审标准疑似缺失，请补齐评分/分档/扣分规则。`
      : null
  // 事实陈述（提取项数 + 分值构成）与判定解耦，异常态同样先给出，
  // 否则账本判 review_required 时用户只剩差额告警、看不到提取结果。
  const headlineText = buildScoringBannerHeadlineText(summary, {
    verified: isOk,
    groupShares: legacyGroupShares,
    // 未归组存在时 legacy shares 含未经关系裁判验证的模型标签，
    // 禁止在主结论回退展示，避免 amber 容器继续陈述错误分组构成。
    suppressShares: (ledgerReport?.unlabeledSum ?? 0) > 0.01,
  })
  const ledgerReviewReason =
    ledgerNeedsReview && ledgerReport ? resolveLedgerReviewReason(ledgerReport) : null
  // 提醒（非警告）——结论自洽但有需用户对照原文确认的信息缺口。
  const notices: string[] = []
  // 按基准来源分述, 不再以单一文案断言"文件未写总分"(doc1933 实为写了但
  // 账本输入未覆盖到该行), 也不再用"推算"描述确定性求和。
  const basisNotice = ledgerSoftBasisOnly ? basisConfidenceNotice(ledgerReport) : null
  if (basisNotice) {
    notices.push(basisNotice)
  }
  if (
    ledgerReport?.inferredGroupLabel &&
    ledgerReport.inferredGroupSum > 0 &&
    ledgerReport.inferredGroupCount > 0
  ) {
    notices.push(
      `分值守恒核对：${ledgerReport.inferredGroupCount} 项共 ${formatScore(ledgerReport.inferredGroupSum)} 分，与「${ledgerReport.inferredGroupLabel}」唯一待归分值一致，当前按该评分卷展示，请对照原文确认`,
    )
  }
  if (ledgerGapIsFoldingArtifact && summary?.folded) {
    notices.push(
      `本文档为折算制：各卷原始满分合计 ${formatScore(summary.folded.rawExpectedTotal)} 分，按权重折算后 ${formatScore(summary.folded.foldedBase)} 分，与招标文件要求一致`,
    )
  }
  // scope 软基准自洽豁免后, 绿色态内保留基准推导提醒(单包 同构)
  if (scopeView?.anyScopeSoftBasisOnly) {
    notices.push(
      '各采购包/标段分值已自洽闭合；评分基准为推导值，请对照招标文件原文确认',
    )
  }
  const assessmentNotice = report
    ? buildSuspectedAssessmentNotice(
        report.suspectedAssessmentCount,
        report.suspectedAssessmentScore,
      )
    : ''
  if (assessmentNotice) {
    notices.push(assessmentNotice)
  }
  // Task7: 三态视觉状态：green=全部通过，blue=中性信息（unresolved且无其他错误），amber=需处理。
  // blue 严格要求 isScopeInfoOnly && !hasOtherRealError：候选失败等真实错误与 unresolved 并存时 → amber。
  // verdict 非空时三态直接取后端权威结论，本地推导仅作回退。
  const visualState: 'green' | 'blue' | 'amber' =
    verdict ?? (isOk ? 'green' : (isScopeInfoOnly && !hasOtherRealError) ? 'blue' : 'amber')
  const containerClass =
    visualState === 'green'
      ? 'mb-4 px-4 py-2.5 bg-green-50 rounded-lg border border-green-200'
      : visualState === 'blue'
        ? 'mb-4 px-4 py-2.5 bg-blue-50 rounded-lg border border-blue-200'
        : 'mb-4 px-4 py-2.5 bg-amber-50 rounded-lg border border-amber-300'
  const iconClass =
    visualState === 'green'
      ? 'w-5 h-5 shrink-0 text-green-500'
      : visualState === 'blue'
        ? 'w-5 h-5 shrink-0 text-blue-500'
        : 'w-5 h-5 shrink-0 text-amber-500'
  const titleClass =
    visualState === 'green'
      ? 'text-sm font-medium text-green-900'
      : visualState === 'blue'
        ? 'text-sm font-medium text-blue-900'
        : 'text-sm font-medium text-amber-900'
  const bodyClass =
    visualState === 'green'
      ? 'text-sm text-green-800'
      : visualState === 'blue'
        ? 'text-sm text-blue-800'
        : 'text-sm text-amber-800'
  const hintClass =
    visualState === 'green'
      ? 'text-xs text-green-700'
      : visualState === 'blue'
        ? 'text-xs text-blue-700'
        : 'text-xs text-amber-600'

  // 组级缺口能被未归卷分值完全解释时, "分值不一致"与"未归卷"说的是
  // 同一批分值, 分开陈述互相否证——合并为单一"未归卷"叙事(标题+一条明细)。
  const unlabeledGapMerge = ledgerReport
    ? mergeUnlabeledIntoGroupGaps(ledgerReport.groupRows, ledgerReport.unlabeledSum)
    : null
  // 组级缺口行仅展示存在实际差额的组(|gap| > 0.01)，闭合组不重复罗列。
  const ledgerGapRows =
    ledgerReport && !unlabeledGapMerge
      ? ledgerReport.groupRows.filter((row) => Math.abs(row.gap) > 0.01)
      : []
  const scoringCompositionText = buildScoringCompositionText(
    ledgerReport ?? null,
    legacyGroupShares.length > 0 ? legacyGroupShares : summary?.shares ?? [],
  )

  // legacy 报告在账本区块面前的让位判定(去重铁律, 见文件头):
  // verdict——账本自带判定标题时, legacy 的同义判定句与泛化建议句全部不渲染;
  // volumeGaps——仅当账本一条差额行都没列(判定原因与差额无关)而 legacy 检出卷缺口时保留;
  // missingRuleHint——账本判定原因已是"缺少评审标准"时不再重复, 否则保留(它带项数)。
  const legacyVerdictVisible =
    reportNeedsReview && !legacyReviewSuperseded && !ledgerNeedsReview
  const legacyVolumeGapsVisible =
    reportNeedsReview &&
    !legacyReviewSuperseded &&
    globalComparisonAllowed &&
    problemVolumes.length > 0 &&
    (!ledgerNeedsReview || ledgerGapRows.length === 0)
  const missingRuleHintVisible =
    effectiveReportNeedsReview && !!missingRuleHint && ledgerReviewReason !== 'insufficiency'
  const legacyDiagnosticsVisible =
    !!report && report.diagnostics.length > 0 && !legacyReviewSuperseded
  const multiSchemeText = ledgerReport
    ? multiSchemeDetail(ledgerReport.multiSchemeSignal, ledgerReviewReason)
    : null
  const compositionReviewVisible =
    !!scoringCompositionText && (ledgerReviewReason === 'unlabeled' || !!unlabeledGapMerge)

  // 第5层 行动区可见性:
  // 聚焦按钮——只要主列表存在评分项/重点项就给, 通过态同样允许用户复核范围;
  // 新增按钮——相对期望总分精确判定（偏低/明确漏提必显；明确多提不显；
  // 语义不清时偏离≥50才显）；文案用中性「新增评分项」。
  const focusActionVisible =
    !!onFocusScoringItems && !!focusSummary && focusSummary.totalCount > 0
  const addScoringItemVisible =
    !!onAddScoringItem &&
    shouldShowAddScoringItem(
      buildScoringAddItemVisibilityInput({ isOk, report, ledgerReport: ledgerReport ?? null }),
    )
  const iconPath = isOk
    ? 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'
    : visualState === 'blue'
      ? 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
      : 'M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
  const scopeDetailsVisible =
    !globalComparisonAllowed &&
    (ledgerReport?.scopeReports.length ?? 0) > 0 &&
    !!scopeView &&
    !(scopeView.allApplicableReconciled && !scopeView.anyApplicableGap && !scopeView.anyUnresolved)
  const hasCollapsibleDetails =
    (!isOk &&
      (legacyVerdictVisible ||
        legacyVolumeGapsVisible ||
        missingRuleHintVisible ||
        (effectiveLedgerNeedsReview && !!ledgerReport))) ||
    notices.length > 0 ||
    legacyDiagnosticsVisible ||
    scopeDetailsVisible
  const actions =
    focusActionVisible || addScoringItemVisible ? (
      <>
        {/* 视觉红线: 聚焦/新增不引入状态图标, 状态语义只由主结论图标承担。 */}
        {focusActionVisible && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onFocusScoringItems}
            title="筛选并定位到评分项与重点评审项"
          >
            聚焦评分项
          </Button>
        )}
        {addScoringItemVisible && (
          <Button
            type="button"
            size="sm"
            onClick={onAddScoringItem}
            className="bg-amber-600 text-white shadow-xs hover:bg-amber-700"
            title="手动新增一条评分项"
          >
            新增评分项
          </Button>
        )}
      </>
    ) : null

  return (
    <ScoringBannerChrome
      containerClass={containerClass}
      iconClass={iconClass}
      iconPath={iconPath}
      hasDetails={hasCollapsibleDetails}
      attentionVisible={visualState === 'amber'}
      headline={headlineText ? <p className={titleClass}>{headlineText}</p> : null}
      actions={actions}
      details={
        <>
        {isOk ? null : (
          <>
            {legacyVerdictVisible && (
              <p className={titleClass}>
                {scoreGapNeedsReview
                  ? '评分项分值与评分表不一致，请人工核对'
                  : '评分项规则信息待完善'}
              </p>
            )}
            {legacyVolumeGapsVisible && (
              <ul className={`mt-1 list-disc space-y-1 pl-5 ${bodyClass}`}>
                {problemVolumes.map(([vol, c]) => (
                  <li key={vol}>{volumeGapText(vol, c.sum, c.expected)}</li>
                ))}
              </ul>
            )}
            {missingRuleHintVisible && <p className={bodyClass}>{missingRuleHint}</p>}
            {legacyVerdictVisible && (
              <p className={hintClass}>
                {scoreGapNeedsReview
                  ? '请对照招标文件的评分表，补齐或修正后再开始审查。'
                  : '分值合计已闭环，可在评分项详情中补充评审标准文本，便于后续审查引用。'}
              </p>
            )}
          </>
        )}
        {/* 账本通过态区块已删除——它与主结论完全重复(两者都在陈述"提取N分、
 校验通过"), 且 ledgerReconciledOk 是 isOk 的子条件, 通过时主结论必然已用
 "已成功提取…并完成分值校验"表达。ledgerReconciledOk 变量保留, 仅参与
 ledgerNeedsReview 判定, 不再独立渲染。 */}
        {/* Task7: scope 级别对账面板（多包模式 globalComparisonAllowed=false）。
 globalComparisonAllowed=true 时组件内部返回 null，零影响单包流程。 */}
        <ScopedScoringReportPanel
          scopes={ledgerReport?.scopeReports ?? []}
          globalComparisonAllowed={globalComparisonAllowed}
        />
        {/* verdict 为权威 green 时本地账本复核块让位，绿色容器内不得出现琥珀告警。
 verdict 缺失时 !isOk 恒为真（effectiveLedgerNeedsReview 蕴含本地非通过），行为不变。 */}
        {!isOk && effectiveLedgerNeedsReview && ledgerReport && (
          // 去术语化: "账本对账/声明基准/低置信"等内部术语改为用户语言,
          // 分级来源(basisLevel)属实现细节, 起不再向用户展示。
          // 标题按真实原因分支——需复核有 6 类原因, 其中 4 类与分值差额无关,
          // 统一渲染"分值不一致"会在 gap=0 时被同屏数字当场否证。
          // Task7: effectiveLedgerNeedsReview 确保 globalComparisonAllowed=false
          // 时此全局块不渲染（禁止出现"招标文件要求1/4/5""已提取200分比要求多"）。
          <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3">
            {compositionReviewVisible ? (
              <>
                <p className="text-sm font-medium text-amber-900">{scoringCompositionText}</p>
                <p className="text-xs text-amber-600">{SCORING_MANUAL_CHECK_NOTICE}</p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-amber-900">
                  {ledgerReviewHeadline(ledgerReviewReason!)}
                </p>
                <p className="text-sm text-amber-800">{ledgerTotalsLine(ledgerReport)}</p>
                {ledgerGapRows.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-amber-800">
                    {ledgerGapRows.map((row) => (
                      <li key={row.label}>{formatLedgerGroupRow(row)}</li>
                    ))}
                  </ul>
                )}
                {/* 去重: 标题已按 multi_scheme 主因表述时不再复述, 且任何
 情况下都不回显后端原始判据串(内含 Σ=200>101 这类内部量)。 */}
                {multiSchemeText && (
                  <p className="text-sm font-medium text-amber-900">{multiSchemeText}</p>
                )}
                {/* : 低置信本身已作主因写进标题时不再重复，仅在其他主因下作补充。 */}
                {ledgerReport.basisLowConfidence && ledgerReviewReason !== 'low_confidence' && (
                  <p className="text-xs text-amber-600">{SCORING_MANUAL_CHECK_NOTICE}</p>
                )}
              </>
            )}
          </div>
        )}
        {/* 第3层 提醒: 结论自洽、仅存在需对照原文确认的信息缺口。
 : 不放圆形图标——主结论图标已表达整体状态, 再放一个圆形 Info
 会与之视觉重复; 蓝色底本身已足够与琥珀警告区分。 */}
        {notices.length > 0 && (
          <div className="space-y-1 rounded-lg border border-blue-100 bg-blue-50/50 px-3 py-2.5">
            {notices.map((text) => (
              <p
                key={text}
                className="text-xs font-medium leading-relaxed text-blue-700"
              >
                {text}
              </p>
            ))}
          </div>
        )}
        {/* 第4层 详情折叠: 仅 report.diagnostics——含"已识别构成：价格 30.0 分
 （第34页）"这类页码定位, 可据此核对原文。逻辑表编号/错误码/基准分级/账本
 diagnostics 等实现细节不进展示层。无诊断时整块不渲染。 */}
        {legacyDiagnosticsVisible && (
          <details className="mt-1">
            <summary
              className={`cursor-pointer select-none text-xs ${hintClass} hover:underline`}
            >
              分值核对明细
            </summary>
            <ul className={`mt-1 list-disc space-y-0.5 pl-5 text-xs ${hintClass}`}>
              {report.diagnostics.map((item) => (
                <li key={item} className="break-all">
                  {item}
                </li>
              ))}
            </ul>
          </details>
        )}
        </>
      }
    />
  )
}
