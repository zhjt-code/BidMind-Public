/**
 * 招标要求确认页（RequirementReview）字段转换 helper。
 *
 * 单独抽出为 .ts 文件以便 `frontend/tests/` 直接导入做契约测试，
 * 同时保持 `RequirementReview.tsx` 仅负责 UI 与交互。
 */
import {
  transformScoringLeafIntegrity,
  type ScoringLeafIntegrityEvidence,
} from './RequirementReview/scoringLeafIntegrity.ts'
import {
  transformScopedReportList,
  type ScopedReport,
} from './RequirementReview/scoringScopeReportTransforms.ts'
import {
  formatExtractionWarning,
  type ExtractionWarningDisplay,
} from './RequirementReview/extractionWarningCopy.ts'

export { formatExtractionWarning }
export type { ExtractionWarningDisplay }

export type { ScopedReport }

export type ReviewRelevance = 'actionable' | 'informational' | 'procedural'

/**
 * 评分项结构化元数据（来自后端 scoring_metadata 字段）。
 * 字段对齐 ExtractedScoringItem + scoring_fidelity 输出。
 */
export interface ScoringMetadata {
  /** 评分项名称（如"技术方案"）。 */
  scoreItemName: string | null
  /** 得分/分档规则列表。 */
  scoreRules: string[]
  /** 扣分/不得分规则列表。 */
  deductionRules: string[]
  /** 所属卷标识（technical/commercial/price 等）。 */
  volume: string | null
  /** 所属卷原文标签（如"商务标"）。 */
  volumeLabel: string | null
  /** 所属采购包/标段（如"采购包1"）。 */
  lot: string | null
  /** 卷权重（0-1，如技术标 50% → 0.5）。 */
  volumeWeight: number | null
  /** 卷满分基准（如技术卷满分 100）。 */
  volumeMax: number | null
  /** 权威原文（提取时识别的权威文本段落）。 */
  authoritativeDescription: string | null
  /** 忠实性标志（ok/truncated/inferred 等）。 */
  fidelityFlag: string | null
  /** 忠实性提示文本。 */
  fidelityNote: string | null
  /** 忠实性重叠信息。 */
  fidelityOverlap: string | null
  /** 疑似履约/运营考核表（确认页建议排除，不打红核计）。 */
  suspectedAssessmentForm: boolean
}

export interface Requirement {
  id: number
  category: string
  description: string
  isMandatory: boolean
  confirmed: boolean
  documentId: number | null
  sourcePage: number | null
  sourcePages: number[]
  mergedCount: number | null
  subRequirements: string[]
  /** 评审意义分级：actionable 进入审查；informational/procedural 默认折叠为参考条款。 */
  reviewRelevance: ReviewRelevance
  /** 评审意义归因：llm_locus/rule_floor/contract_section/behavioral_mandatory/manual；actionable 为 null。 */
  relevanceReason: string | null
  /** 要求种类：normal 普通要求 / scoring_item 评分项。 */
  requirementKind: string
  /** 评分项满分分值（仅 scoring_item）。 */
  scoreWeight: number | null
  /** 是否重点评审项（评分项默认 true）。 */
  isKeyReview: boolean
  /** 展示分组下的全部底层要求 ID（折叠复合拆分子面）；确认/编辑/删除按此整组 fan-out。 */
  memberIds: number[]
  /** 评分项结构化元数据（仅 scoring_item 有值，普通要求为 null）。 */
  scoringMetadata: ScoringMetadata | null
}

/**
 * 将后端 scoring_metadata 原始对象转换为 ScoringMetadata；
 * raw 为 null/非对象时返回 null。
 */
export function transformScoringMetadata(raw: unknown): ScoringMetadata | null {
  if (!raw || typeof raw !== 'object') return null
  const m = raw as Record<string, unknown>
  const toStringArray = (v: unknown): string[] =>
    Array.isArray(v) ? (v as unknown[]).filter((x): x is string => typeof x === 'string') : []
  const toStr = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
  return {
    scoreItemName: toStr(m.score_item_name),
    scoreRules: toStringArray(m.score_rules),
    deductionRules: toStringArray(m.deduction_rules),
    volume: toStr(m.volume),
    volumeLabel: toStr(m.volume_label),
    lot: toStr(m.lot),
    volumeWeight: toOptionalNumber(m.volume_weight),
    volumeMax: toOptionalNumber(m.volume_max),
    authoritativeDescription: toStr(m.authoritative_description),
    fidelityFlag: toStr(m.fidelity_flag),
    fidelityNote: toStr(m.fidelity_note),
    fidelityOverlap: toStr(m.fidelity_overlap),
    suspectedAssessmentForm: m.suspected_assessment_form === true,
  }
}

/**
 * 格式化评分/扣分规则列表为可读字符串（规则条目用换行分隔）。
 * 空列表返回空字符串。
 */
export function formatScoringRules(rules: string[]): string {
  if (rules.length === 0) return ''
  return rules.join('\n')
}

export function normalizeReviewRelevance(value: unknown): ReviewRelevance {
  if (value === 'informational' || value === 'procedural') return value
  return 'actionable'
}


/**
 * 归一化招标要求来源页码：
 * - 优先消费 source_pages 数组
 * - 数组为空或全部非法时回退到 source_page
 * - 过滤非数字 / NaN / 0 / 负数等非法值
 * - 去重并按升序输出
 */
export function normalizeRequirementPages(
  rawPages: unknown,
  fallbackPage: unknown,
): number[] {
  const collected: number[] = []
  if (Array.isArray(rawPages)) {
    for (const item of rawPages) {
      const num = typeof item === 'number' ? item : Number(item)
      if (Number.isFinite(num) && num >= 1) {
        collected.push(Math.floor(num))
      }
    }
  }
  if (collected.length === 0 && fallbackPage != null) {
    const num = typeof fallbackPage === 'number' ? fallbackPage : Number(fallbackPage)
    if (Number.isFinite(num) && num >= 1) {
      collected.push(Math.floor(num))
    }
  }
  return Array.from(new Set(collected)).sort((a, b) => a - b)
}

export function toOptionalNumber(value: unknown): number | null {
  if (value == null) return null
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) ? num : null
}

export function toOptionalPage(value: unknown): number | null {
  const num = toOptionalNumber(value)
  if (num == null || num < 1) return null
  return Math.floor(num)
}

/** 单卷满分校验明细。 */
export interface VolumeCheck {
  sum: number
  expected: number
  weight: number
  within: boolean
}

/** 评分项"按卷满分校验"报告（第3层，供确认页人工兜底提示）。 */
export interface ScoringTotalsReport {
  needsReview: boolean
  note: string
  byVolume: Record<string, number>
  /** 各采购包/标段满分合计（：多包文档按包独立核计，单包文档为空对象）。 */
  byLot: Record<string, number>
  volumeChecks: Record<string, VolumeCheck>
  weightSumOk: boolean
  itemCount: number
  missingScoreCount: number
  missingRuleCount: number
  structureReviewRequired: boolean
  diagnostics: string[]
  authorityCount: number
  llmCount: number
  /** 疑似履约考核项数（已从满分合计中排除）。 */
  suspectedAssessmentCount: number
  /** 疑似履约考核合计分值。 */
  suspectedAssessmentScore: number
}

/** 卷标识 → 中文卷名（与后端 _VOLUME_LABELS 对齐）。 */
const VOLUME_LABELS: Record<string, string> = {
  technical: '技术标',
  price: '价格',
  commercial: '商务标',
  service: '服务',
  qualification: '资格',
  staffing: '人员',
  performance: '业绩',
  other: '其他',
  unspecified: '其他',
}

export function volumeLabel(volume: string): string {
  return VOLUME_LABELS[(volume || '').toLowerCase()] || volume || '其他'
}

/**
 * 单卷满分校验方向性提示文案。
 *
 * 高于基准（重复提取/识别错误）与低于基准（漏识别）是相反的归因，
 * 不能机械统一用"漏识别"描述——超出 100 分的卷不可能是"漏"。
 */
export function volumeCheckHint(check: VolumeCheck): string {
  if (check.within) return '，正常'
  if (check.sum > check.expected) return '，超出基准，可能重复提取或识别错误'
  return '，低于基准，可能有评分项漏识别'
}

/** 分值展示：整数去小数、非整数去尾随 0（对齐后端 round(_, 4)），避免出现 "100.0 分"。 */
export function formatScore(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return String(Number(value.toFixed(4)))
}

/** 单卷分值占比明细。 */
export interface ScoringVolumeShare {
  label: string
  score: number
}

/** 折算制的量化口径：供判定账本差额是否纯由"未折算"造成。 */
export interface ScoringFoldedBasis {
  /** 展示文案，如“技术标 100分×40%、价格 100分×60%，折算基准100分”。 */
  text: string
  /** 各卷 expected×weight 之和，即折算后应得总分（本例 100）。 */
  foldedBase: number
  /** 各卷 expected 直接相加，即事实层未折算合计（本例 200）。 */
  rawExpectedTotal: number
}

/** 通过态摘要：供确认页展示 "总分 + 分卷明细"，替代原静态文案。 */
export interface ScoringTotalsSummary {
  itemCount: number
  totalScore: number
  /** 折算制通过态文案，如“技术标 100分×50%...”，避免把卷内满分机械显示为总分300。 */
  foldedText: string | null
  /** 折算制量化口径；非折算制为 null。 */
  folded: ScoringFoldedBasis | null
  /** 各卷分值明细；仅存在多个有效卷时给出，单卷/全局制为空数组（避免与总分重复）。 */
  shares: ScoringVolumeShare[]
/**
 * 各采购包合计明细：多包文档（>=2 包）时给出，横幅须按包展示
 * "采购包1 合计 100 分、采购包2 合计 100 分"，禁止把两包相加成"总分 200 分"误导。
 */
  lotShares: ScoringVolumeShare[]
}

/**
 * 由报告构造通过态摘要：总分为各卷分值之和（等于后端 total），
 * 分卷明细按分值降序，单卷/全局制不给明细；多包文档另给各包合计明细。
 */
export function buildScoringTotalsSummary(report: ScoringTotalsReport): ScoringTotalsSummary {
  const entries = Object.entries(report.byVolume)
    .map(([volume, score]) => ({ label: volumeLabel(volume), score: Number(score) || 0 }))
    .filter((entry) => entry.score > 0)
  const totalScore = Number(
    entries.reduce((sum, entry) => sum + entry.score, 0).toFixed(4),
  )
  const shares = entries.length > 1 ? [...entries].sort((a, b) => b.score - a.score) : []
  const lotEntries = Object.entries(report.byLot)
    .map(([lot, score]) => ({ label: lot, score: Number(score) || 0 }))
    .filter((entry) => entry.score > 0)
  const lotShares =
    lotEntries.length > 1
      ? [...lotEntries].sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'))
      : []
  const folded = buildFoldedScoringBasis(report)
  return {
    itemCount: report.itemCount,
    totalScore,
    foldedText: folded?.text ?? null,
    folded,
    shares,
    lotShares,
  }
}

function buildFoldedScoringBasis(
  report: ScoringTotalsReport,
): ScoringFoldedBasis | null {
  if (report.needsReview || !report.weightSumOk || Object.keys(report.byLot).length > 1) {
    return null
  }
  const checks = Object.entries(report.volumeChecks)
    .filter(([, check]) => check.within && check.weight > 0 && check.sum > 0)
  if (checks.length < 2) return null
  const weightTotal = checks.reduce((sum, [, check]) => sum + check.weight, 0)
  if (Math.abs(weightTotal - 1) > 0.02) return null
  const expectedTotal = checks.reduce((sum, [, check]) => sum + check.expected, 0)
  if (Math.abs(expectedTotal - 100) <= 1) return null
  const foldedBase = checks.reduce((sum, [, check]) => sum + check.expected * check.weight, 0)
  const detail = checks
    .map(([volume, check]) => (
      `${volumeLabel(volume)} ${formatScore(check.sum)}分×${formatScore(check.weight * 100)}%`
    ))
    .join('、')
  return {
    text: `${detail}，折算基准${formatScore(foldedBase)}分`,
    foldedBase: Number(foldedBase.toFixed(4)),
    rawExpectedTotal: Number(expectedTotal.toFixed(4)),
  }
}

export function transformScoringTotalsReport(raw: unknown): ScoringTotalsReport | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  // diagnostics 作为列表 key 直接渲染，保序去重兜底——
  // 即便后端回归产生重复行，也不重复展示且不触发 React 重复 key。
  const diagnostics = Array.isArray(r.diagnostics)
    ? Array.from(
        new Set(
          (r.diagnostics as unknown[]).filter(
            (item): item is string => typeof item === 'string',
          ),
        ),
      )
    : []
  return {
    needsReview: Boolean(r.needs_review),
    note: typeof r.note === 'string' ? r.note : '',
    byVolume: (r.by_volume as Record<string, number> | undefined) ?? {},
    byLot: (r.by_lot as Record<string, number> | undefined) ?? {},
    volumeChecks: (r.volume_checks as Record<string, VolumeCheck> | undefined) ?? {},
    weightSumOk: r.weight_sum_ok !== false,
    itemCount: typeof r.item_count === 'number' ? (r.item_count as number) : 0,
    missingScoreCount:
      typeof r.missing_score_count === 'number' ? (r.missing_score_count as number) : 0,
    missingRuleCount:
      typeof r.missing_rule_count === 'number' ? (r.missing_rule_count as number) : 0,
    structureReviewRequired: r.structure_review_required === true,
    diagnostics,
    authorityCount: typeof r.authority_count === 'number' ? (r.authority_count as number) : 0,
    llmCount: typeof r.llm_count === 'number' ? (r.llm_count as number) : 0,
    suspectedAssessmentCount:
      typeof r.suspected_assessment_count === 'number'
        ? (r.suspected_assessment_count as number)
        : 0,
    suspectedAssessmentScore:
      typeof r.suspected_assessment_score === 'number'
        ? (r.suspected_assessment_score as number)
        : 0,
  }
}

// ===== 通用取值助手（评分账本报告等消费） =====

function toFiniteNumberOr<T>(value: unknown, fallback: T): number | T {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? (value as unknown[]).filter((item): item is string => typeof item === 'string')
    : []
}

// ===== 评分账本对账报告（P5-1：换轴重构，冻结窗口内与 legacy 报告并存展示） =====

export type ScoringLedgerStatus = 'reconciled' | 'review_required'
export type ScoringLedgerBasisLevel =
  | 'l1_explicit_group'
  | 'l2_derived_group'
  | 'l3_doc_total'
  | 'l4_none'

/** 单组对账行：声明分值 vs 提取合计 vs 差额。 */
export interface ScoringLedgerGroupRow {
  label: string
  declared: number
  extractedSum: number
  gap: number
}

export interface ScoringLedgerReport {
  status: ScoringLedgerStatus
  basisLevel: ScoringLedgerBasisLevel
  basisLowConfidence: boolean
  expectedTotal: number | null
  extractedTotal: number
/**
 * 折算制下与 expectedTotal 同轴的提取合计。
 * 非折算制或覆盖不完整时为 null——此时两侧不同轴，禁止相减当差额。
 */
  extractedTotalFolded: number | null
  totalGap: number | null
  groupRows: ScoringLedgerGroupRow[]
  unlabeledSum: number
  multiSchemeSignal: string
  insufficiencyCount: number
  inferredGroupLabel: string | null
  inferredGroupSum: number
  inferredGroupCount: number
  diagnostics: string[]
/**
 * Task7: 多包/标段场景下，各 scope 的独立对账结果列表。
 * 单包文档或未携带 scope_reports 时为空数组。
 */
  scopeReports: ScopedReport[]
/**
 * Task7: false = 多包模式，全局 ledgerTotalsLine/ledgerReviewHeadline 不得渲染；
 * true（默认）= 单包/全局模式，沿用旧逻辑。
 */
  globalComparisonAllowed: boolean
  /** : 叶节点逐项完整性核验证据；后端未携带或无法解析时为默认未 verified。 */
  leafIntegrity: ScoringLeafIntegrityEvidence
}

/** 账本对账状态 → 中文（红色清单：结果文案中文化，禁止英文枚举裸出）。 */
const LEDGER_STATUS_LABELS: Record<ScoringLedgerStatus, string> = {
  reconciled: '对账通过',
  review_required: '需复核',
}

export function ledgerStatusLabel(status: ScoringLedgerStatus): string {
  return LEDGER_STATUS_LABELS[status]
}

/** 账本基准层级 → 中文（红色清单：结果文案中文化，禁止英文枚举裸出）。 */
// 用户语言优先——原标签("显式组级声明"/"组标签推导")是账本内部分级术语,
// 用户无从理解也无法据此行动; 改为描述"分值要求从哪里读到的"。
const LEDGER_BASIS_LEVEL_LABELS: Record<ScoringLedgerBasisLevel, string> = {
  l1_explicit_group: '招标文件逐卷写明的分值',
  // "推算"暗示估计成分, 实际是对已提取评分项的确定性归集; 真正的弱点是
  // 未经原文交叉核对(同义反复), 文案应指向该弱点而非贬低计算过程。
  l2_derived_group: '由已提取评分项归集（未经原文核对）',
  l3_doc_total: '招标文件写明的总分',
  l4_none: '未在招标文件中找到分值说明',
}

export function ledgerBasisLevelLabel(level: ScoringLedgerBasisLevel): string {
  return LEDGER_BASIS_LEVEL_LABELS[level]
}

/** 带符号分值展示（组差额 gap 用）：正数补 "+"，负数自带 "-"。 */
export function formatSignedScore(value: number): string {
  return value > 0 ? `+${formatScore(value)}` : formatScore(value)
}

/**
 * 总差额方向性提示文案（教训）：
 * 负差额（提取 < 基准）= 可能漏提；正差额 = 可能重复提取或多方案并存，
 * 两个方向归因相反，禁止机械共用同一文案。
 */
export function ledgerTotalGapHint(totalGap: number): string {
  // 去术语化: "差额/基准"改为"比要求多/少"。
  if (totalGap < 0) {
    return `比要求少 ${formatScore(Math.abs(totalGap))} 分，可能有评分项没提取到`
  }
  return `比要求多 ${formatScore(totalGap)} 分，可能有重复提取或存在多套评分方案`
}

/**
 * 评分项事实陈述：无论校验是否通过都必须先告知"提取了多少项、
 * 分值如何构成"——这是与判定无关的客观事实。
 *
 * 此前该文案只在 isOk 分支渲染，账本判 review_required 时用户只能看到差额
 * 告警而完全看不到提取结果（doc1923 实测）。`verified` 只决定措辞是否含
 * "并完成分值校验"，不决定是否展示。
 *
 * 多包文档按包展示各包合计（：禁止相加成"总分200分"）；折算制展示
 * "卷内满分×权重"（两层语义：事实层为各卷原始满分，不得机械折算）。
 */
export function buildScoringHeadlineText(
  summary: ScoringTotalsSummary | null,
  options: { verified: boolean; groupShares?: ScoringVolumeShare[] },
): string {
  if (!summary) return ''
  const lead = options.verified
    ? `已成功提取 ${summary.itemCount} 个评分项并完成分值校验`
    : `已提取 ${summary.itemCount} 个评分项`
  if (summary.lotShares.length > 0) {
    const lots = summary.lotShares
      .map((share) => `${share.label} 合计 ${formatScore(share.score)} 分`)
      .join('、')
    return `${lead}，${lots}。`
  }
  if (summary.foldedText) {
    return `${lead}，${summary.foldedText}。`
  }
  // 原文组构成(如"设计方案 26 分、施工方案 30 分")优先于枚举卷构成——
  // volume 六类枚举会把 doc1933 的 5 个原文大类压成"技术标 70 分、价格 30 分",
  // 用户无法按招标文件的评审维度核对。仅在组行完整覆盖提取分值时才替换
  // (完整性由 scoringGroupShares.ledgerGroupShares 把关)。
  const shares =
    options.groupShares && options.groupShares.length > 0
      ? options.groupShares
      : summary.shares
  const shareText =
    shares.length > 0
      ? `，其中 ${shares
          .map((share) => `${share.label} ${formatScore(share.score)} 分`)
          .join('、')}`
      : ''
  return `${lead}，总分 ${formatScore(summary.totalScore)} 分${shareText}。`
}

/**
 * 判定账本差额是否"纯由未折算造成的假差额"。
 *
 * 折算制文档的事实层合计是各卷原始满分之和（100+100=200， 两层语义），
 * 而账本声明基准是折算后总分（100）。当两侧对"应得多少分"并无分歧时，账本报出的
 * 差额只是它拿折算后基准去比未折算合计的产物，不存在任何需用户处理的问题——
 * 此前在这种情况下仍向用户报"需复核"，且文案宣称"两者口径不同"，与横幅首行
 * 已给出的"折算基准100分"自相矛盾（用户实测反馈）。
 *
 * 三个条件必须同时满足，缺一即视为真实缺口正常告警：
 * 1. 折算制且 legacy 按卷满分校验已通过（folded 非空即隐含 needsReview=false）；
 * 2. 对"应得总分"无分歧：legacy 折算基准 ≡ 账本声明基准；
 * 3. 差额恰好等于未折算造成的量（rawExpectedTotal - foldedBase），不得混入偏差。
 *
 * 条件 3 是关键防线：若实际漏提使差额偏离该值（如 200 应得却只提到 190），
 * 差额不再等于折算量，判定即为 false，告警照常展示。
 */
export function isLedgerGapFoldingArtifact(
  folded: ScoringFoldedBasis | null,
  ledger: ScoringLedgerReport | null,
): boolean {
  if (!folded || !ledger) return false
  if (ledger.expectedTotal == null || ledger.totalGap == null) return false
  if (Math.abs(folded.foldedBase - ledger.expectedTotal) > 0.01) return false
  const foldingArtifact = folded.rawExpectedTotal - folded.foldedBase
  return Math.abs(ledger.totalGap - foldingArtifact) <= 0.01
}

/**
 * 账本需复核的真实原因。
 *
 * 后端 `_collect_review_reasons` 有 6 类互不相同的触发原因，其中 4 类与分值差额
 * 无关（多方案 / 捕获冲突 / 证据不足 / 基准层级或置信度不足）。此前前端对所有
 * 原因统一渲染"分值合计与招标文件要求不一致"，在 doc1925(项目2443) 上出现
 * "要求 100 分，已提取 100 分"却报不一致的自我否证。
 *
 * 优先级：实际数值问题优先于元信息问题——用户对"分值对不上"能立即行动，
 * 而"基准未经原文交叉核对"只需对照确认。
 */
export type LedgerReviewReason =
  | 'total_gap'
  | 'group_gap'
  | 'multi_scheme'
  | 'unlabeled'
  | 'insufficiency'
  | 'low_confidence'
  | 'other'

export function resolveLedgerReviewReason(
  ledger: ScoringLedgerReport,
): LedgerReviewReason {
  if (ledger.totalGap != null && Math.abs(ledger.totalGap) > 0.01) return 'total_gap'
  if (ledger.groupRows.some((row) => Math.abs(row.gap) > 0.01)) return 'group_gap'
  if (ledger.multiSchemeSignal !== '') return 'multi_scheme'
  if (ledger.unlabeledSum > 0.01) return 'unlabeled'
  if (ledger.insufficiencyCount > 0) return 'insufficiency'
  if (ledger.basisLowConfidence || ledger.basisLevel === 'l2_derived_group') {
    return 'low_confidence'
  }
  if (ledger.basisLevel === 'l4_none') return 'low_confidence'
  return 'other'
}

const LEDGER_REVIEW_HEADLINES: Record<LedgerReviewReason, string> = {
  total_gap: '分值合计与招标文件要求不一致，请核对',
  group_gap: '部分评分卷的分值与招标文件要求不一致，请核对',
  multi_scheme: '检测到多套评分方案，请确认本项目适用哪一套',
  unlabeled: '评分项分值构成需核实',
  insufficiency: '部分评分项缺少评审标准，建议补充后再开始审查',
  // 该文案仅在 expectedTotal 为空(L4 无任何基准)时可达——其余低置信来源
  // (L1默认百分制/L2归集/L3多候选)均判自洽, 走 scoringBasisNotice 的分述提醒。
  // 故此处只表达"未找到总分说明", 不再用单一文案覆盖四类来源, 也不用"推算"。
  low_confidence: '注意：请仔细对照招标文件核实',
  other: '评分项分值需人工确认',
}

/** 需复核标题文案：按真实原因取，禁止用单一文案覆盖全部原因。 */
export function ledgerReviewHeadline(reason: LedgerReviewReason): string {
  return LEDGER_REVIEW_HEADLINES[reason]
}

/**
 * 判定"系统内部自洽、仅基准来源不可靠"。
 *
 * leaf 强证据还须满足口径与 scope 模式一致，且无多方案/充分性缺口；
 * 无 leaf 时沿用 的 low_confidence + gap≈0 + 基准非空路径。
 */
export function isLedgerSelfConsistentWithSoftBasis(
  ledger: ScoringLedgerReport | null,
  report: ScoringTotalsReport | null = null,
): boolean {
  if (!ledger) return false
  const leafBasisMatchesScope = ledger.leafIntegrity?.basis ===
    (ledger.globalComparisonAllowed ? 'single_100' : 'per_scope_100')
  const leafReportClean = !report || (report.missingScoreCount === 0 && report.missingRuleCount === 0 && !report.structureReviewRequired)
  if (
    ledger.leafIntegrity?.verified &&
    leafBasisMatchesScope &&
    leafReportClean &&
    ledger.multiSchemeSignal === '' &&
    ledger.insufficiencyCount === 0
  ) return true
  if (resolveLedgerReviewReason(ledger) !== 'low_confidence') return false
  if (ledger.expectedTotal == null) return false
  return ledger.totalGap != null && Math.abs(ledger.totalGap) <= 0.01
}

/**
 * 分值对比行文案：基准来源决定措辞。
 *
 * L1/L3 是招标文件明写，可称"招标文件要求"；L2 是对已提取评分项的归集结果，称
 * "招标文件要求"属虚假归因；L4 无基准时不得凭空给出要求值。
 */
export function ledgerTotalsLine(ledger: ScoringLedgerReport): string {
  // 折算制下 extractedTotal 是各卷原始满分之和(如 300)，而基准是折算后的
  // 总分(如 100)。直接并列两个数字会让用户看到"要求 100 分，已提取 300 分，两者一致"
  // 这种自相矛盾的句子，故必须显式说明两个量分属原始轴与折算轴。
  const folded = ledger.extractedTotalFolded
  const extracted =
    folded != null && Math.abs(folded - ledger.extractedTotal) > 0.01
      ? `各卷原始满分合计 ${formatScore(ledger.extractedTotal)} 分，按权重折算后 ${formatScore(folded)} 分`
      : `已提取 ${formatScore(ledger.extractedTotal)} 分`
  if (ledger.expectedTotal == null || ledger.basisLevel === 'l4_none') {
    return extracted
  }
  const derived = ledger.basisLevel === 'l2_derived_group'
  const label = derived ? '归集总分' : '招标文件要求'
  let gapPart = ''
  if (ledger.totalGap != null && Math.abs(ledger.totalGap) > 0.01) {
    gapPart = `，${ledgerTotalGapHint(ledger.totalGap)}`
  } else if (!derived) {
    // 仅当基准来自招标文件原文时，"一致"才是有效结论；L2 归集基准下二者
    // 必然相等（同义反复），标注"一致"会被误读为核对通过。
    gapPart = '，两者一致'
  }
  return `${label} ${formatScore(ledger.expectedTotal)} 分，${extracted}${gapPart}`
}

/** 单卷缺口行文案：要求 / 已提取 / 差额三要素齐备（去术语化）。 */
export function formatLedgerGroupRow(row: ScoringLedgerGroupRow): string {
  const direction =
    row.gap > 0 ? `多 ${formatScore(row.gap)} 分` : `少 ${formatScore(-row.gap)} 分`
  return `${row.label}：要求 ${formatScore(row.declared)} 分，已提取 ${formatScore(
    row.extractedSum,
  )} 分（${direction}）`
}

function transformLedgerGroupRows(raw: unknown): ScoringLedgerGroupRow[] {
  if (!Array.isArray(raw)) return []
  return (raw as unknown[])
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    .map((item) => ({
      label: typeof item.label === 'string' ? item.label.trim() : '',
      declared: toFiniteNumberOr(item.declared, 0),
      extractedSum: toFiniteNumberOr(item.extracted_sum, 0),
      gap: toFiniteNumberOr(item.gap, 0),
    }))
    // label 直接用作 React 列表 key；非 string / 空白行无法稳定渲染，整行丢弃
    .filter((row) => row.label !== '')
}

/**
 * 将后端透传的 scoring_ledger_report 原始对象转换为 ScoringLedgerReport。
 * raw 非 object / status 或 basis_level 不在枚举内时返回 null（视为无账本报告可用）。
 */
export function transformScoringLedgerReport(raw: unknown): ScoringLedgerReport | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const status = r.status
  if (status !== 'reconciled' && status !== 'review_required') {
    return null
  }
  const basisLevel = r.basis_level
  if (
    basisLevel !== 'l1_explicit_group' &&
    basisLevel !== 'l2_derived_group' &&
    basisLevel !== 'l3_doc_total' &&
    basisLevel !== 'l4_none'
  ) {
    return null
  }
  // diagnostics 作为列表 key 直接渲染，保序去重兜底（同 transformScoringTotalsReport）
  const diagnostics = Array.isArray(r.diagnostics)
    ? Array.from(new Set(toStringArray(r.diagnostics)))
    : []
  return {
    status,
    basisLevel,
    basisLowConfidence: Boolean(r.basis_low_confidence),
    expectedTotal: toFiniteNumberOr(r.expected_total, null),
    extractedTotal: toFiniteNumberOr(r.extracted_total, 0),
    extractedTotalFolded: toFiniteNumberOr(r.extracted_total_folded, null),
    totalGap: toFiniteNumberOr(r.total_gap, null),
    groupRows: transformLedgerGroupRows(r.group_rows),
    unlabeledSum: toFiniteNumberOr(r.unlabeled_sum, 0),
    multiSchemeSignal:
      typeof r.multi_scheme_signal === 'string' ? r.multi_scheme_signal.trim() : '',
    insufficiencyCount: toFiniteNumberOr(r.insufficiency_count, 0),
    inferredGroupLabel:
      typeof r.inferred_group_label === 'string' && r.inferred_group_label.trim()
        ? r.inferred_group_label.trim()
        : null,
    inferredGroupSum: toFiniteNumberOr(r.inferred_group_sum, 0),
    inferredGroupCount: toFiniteNumberOr(r.inferred_group_count, 0),
    diagnostics,
    scopeReports: transformScopedReportList(r.scope_reports),
    globalComparisonAllowed: r.global_comparison_allowed !== false,
    leafIntegrity: transformScoringLeafIntegrity(r.leaf_integrity),
  }
}

export function transformRequirement(item: Record<string, unknown>): Requirement {
  const sourcePages = normalizeRequirementPages(item.source_pages, item.source_page)
  const subRequirements = Array.isArray(item.sub_requirements)
    ? (item.sub_requirements as unknown[]).filter(
        (entry): entry is string => typeof entry === 'string' && entry.trim() !== '',
      )
    : []
  const id = item.id as number
  const memberIds = Array.isArray(item.member_ids)
    ? (item.member_ids as unknown[])
        .map((entry) => (typeof entry === 'number' ? entry : Number(entry)))
        .filter((entry) => Number.isFinite(entry))
    : []
  return {
    id,
    category: item.category as string,
    description: item.description as string,
    isMandatory: Boolean(item.is_mandatory),
    confirmed: Boolean(item.confirmed),
    documentId: toOptionalNumber(item.document_id),
    sourcePage: toOptionalPage(item.source_page),
    sourcePages,
    mergedCount: toOptionalNumber(item.merged_count),
    subRequirements,
    reviewRelevance: normalizeReviewRelevance(item.review_relevance),
    relevanceReason: typeof item.relevance_reason === 'string' ? item.relevance_reason : null,
    requirementKind: (item.requirement_kind as string) || 'normal',
    scoreWeight: toOptionalNumber(item.score_weight),
    isKeyReview: Boolean(item.is_key_review),
    memberIds: memberIds.length > 0 ? memberIds : [id],
    scoringMetadata: transformScoringMetadata(item.scoring_metadata),
  }
}
