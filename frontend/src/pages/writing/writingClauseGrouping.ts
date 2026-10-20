/**
 * 职责: 约束确认页的风险分级分组纯逻辑(★判定/分组切分/展示顺序/折叠默认/组级id收集)
 * 不做: 不渲染UI、不调API、不管理状态、不修改约束数据
 */
import type { WritingClause } from '../../services/writingClauseTransforms'

/** 实质性条款拆分组的稳定 key(区别于普通 disqualification 组) */
export const SUBSTANTIVE_GROUP_KEY = 'disqualification__substantive'

/** ★拆分组展示标题(展示层使用; 分组逻辑自身不消费, 故不引入 tab 文案依赖) */
export const SUBSTANTIVE_GROUP_TITLE = '实质性条款（★必须响应）'

/** 无分值评分说明拆分组的稳定 key(与有分值"评分标准"组区分) */
export const SCORING_UNSCORED_GROUP_KEY = 'scoring_criterion__unscored'

/** 无分值评分说明组展示标题(评标办法总述/格式/保证金/报价等告知类, 非有效评分项) */
export const SCORING_UNSCORED_GROUP_TITLE = '评分说明（无分值 · 仅供参考）'

/** 疑似履约考核评分项拆分组的稳定 key（仍提取，不默认折叠） */
export const SCORING_ASSESSMENT_GROUP_KEY = 'scoring_criterion__assessment'

/** 疑似履约考核组展示标题（食堂/月度服务质量等非评标表） */
export const SCORING_ASSESSMENT_GROUP_TITLE = '疑似履约考核，建议排除'

/** AI/人工分级为参考内容的稳定 key(不进入正文匹配与覆盖分母) */
export const WRITING_REFERENCE_GROUP_KEY = 'writing_relevance__reference'

/** 参考内容组展示标题 */
export const WRITING_REFERENCE_GROUP_TITLE = '参考内容（AI判定不进正文匹配）'

/** 仅由一致性检查兜底的废标红线稳定 key */
export const WRITING_CHECK_ONLY_GROUP_KEY = 'writing_relevance__check_only'

/** 检查型红线组展示标题 */
export const WRITING_CHECK_ONLY_GROUP_TITLE = '检查型红线（由一致性检查兜底）'

/** 与第一期提取 Prompt 的实质性标记集对齐 */
const SUBSTANTIVE_MARK_RE = /[★▲☆]/

/** 组展示顺序: ★废标置顶, 其后按各层 sub_type 既有顺序排列 */
const GROUP_DISPLAY_ORDER: string[] = [
  SUBSTANTIVE_GROUP_KEY,
  'disqualification',
  'fixed_template',
  'format_rule',
  'submission_rule',
  'submission_notice',
  'directory_rule',
  'material_required',
  'content_required',
  'table_format',
  'scoring_criterion',
  SCORING_ASSESSMENT_GROUP_KEY,
  SCORING_UNSCORED_GROUP_KEY,
  'project_context',
  'emphasis_point',
  'differentiation_hint',
  'industry_profile',
  WRITING_CHECK_ONLY_GROUP_KEY,
  WRITING_REFERENCE_GROUP_KEY,
]

/** 低确认价值组(写作参考性质), 默认折叠以聚焦高风险审阅 */
const DEFAULT_COLLAPSED_GROUPS = new Set<string>([
  'emphasis_point',
  'differentiation_hint',
  'project_context',
  SCORING_UNSCORED_GROUP_KEY,
  WRITING_REFERENCE_GROUP_KEY,
  WRITING_CHECK_ONLY_GROUP_KEY,
])

export interface ClauseDisplayGroup {
  /** 分组稳定标识(折叠状态 map / React key) */
  key: string
  /** 原始 sub_type(★组归属 disqualification; 无分值评分组归属 scoring_criterion) */
  subType: string
  /** 是否为实质性(★)拆分组; 展示层据此决定标题与强调样式 */
  isSubstantive: boolean
  /** 展示标题覆盖(拆分组用; 为空时展示层回退 SUB_TYPE_LABELS[subType]) */
  titleOverride?: string
  /** 组内约束(保留传入顺序) */
  clauses: WritingClause[]
  /** 是否默认折叠 */
  collapsedByDefault: boolean
}

/**
 * 判定是否为实质性条款(★/▲/☆)。仅对 disqualification 生效,
 * rawText 优先(证据闸门保障的原样摘录), content 兜底。
 */
export function isSubstantiveClause(clause: WritingClause): boolean {
  if (clause.subType !== 'disqualification') return false
  if (clause.rawText && SUBSTANTIVE_MARK_RE.test(clause.rawText)) return true
  return SUBSTANTIVE_MARK_RE.test(clause.content || '')
}

/**
 * 判定是否为"无分值评分说明"。仅对 scoring_criterion 生效:
 * 无有效正分值(score_weight 非正数)即视为告知类, 不属有效评分项。
 */
export function isUnscoredScoringClause(clause: WritingClause): boolean {
  if (clause.subType !== 'scoring_criterion') return false
  return !(typeof clause.scoreWeight === 'number' && clause.scoreWeight > 0)
}

/** 疑似履约/运营考核表评分项。仅 scoring_criterion 且落库标记为真时生效。 */
export function isSuspectedAssessmentClause(clause: WritingClause): boolean {
  if (clause.subType !== 'scoring_criterion') return false
  return clause.metadataJson?.suspected_assessment_form === true
}

/** AI 判定为参考的约束单独折叠展示; manual 恢复视为正文约束。 */
export function isReferenceWritingClause(clause: WritingClause): boolean {
  if (clause.metadataJson?.writing_relevance_source === 'manual') return false
  return clause.metadataJson?.writing_relevance === 'reference'
}

export function isCheckOnlyWritingClause(clause: WritingClause): boolean {
  if (clause.metadataJson?.writing_relevance_source === 'manual') return false
  return clause.metadataJson?.writing_relevance === 'check_only'
}

/** 是否默认折叠该组 */
export function isGroupCollapsedByDefault(groupKey: string): boolean {
  return DEFAULT_COLLAPSED_GROUPS.has(groupKey)
}

function buildGroup(key: string, clauses: WritingClause[]): ClauseDisplayGroup {
  const isSubstantive = key === SUBSTANTIVE_GROUP_KEY
  const isUnscoredScoring = key === SCORING_UNSCORED_GROUP_KEY
  const isAssessmentScoring = key === SCORING_ASSESSMENT_GROUP_KEY
  const isReferenceGroup = key === WRITING_REFERENCE_GROUP_KEY
  const isCheckOnlyGroup = key === WRITING_CHECK_ONLY_GROUP_KEY
  const subType = isSubstantive
    ? 'disqualification'
    : (isUnscoredScoring || isAssessmentScoring)
      ? 'scoring_criterion'
      : (isReferenceGroup || isCheckOnlyGroup)
        ? (clauses[0]?.subType || key)
        : key
  const titleOverride = isSubstantive
    ? SUBSTANTIVE_GROUP_TITLE
    : isUnscoredScoring
      ? SCORING_UNSCORED_GROUP_TITLE
      : isAssessmentScoring
        ? SCORING_ASSESSMENT_GROUP_TITLE
        : isReferenceGroup
          ? WRITING_REFERENCE_GROUP_TITLE
          : isCheckOnlyGroup
            ? WRITING_CHECK_ONLY_GROUP_TITLE
            : undefined
  return {
    key,
    subType,
    isSubstantive,
    titleOverride,
    clauses,
    collapsedByDefault: isGroupCollapsedByDefault(key),
  }
}

/**
 * 将某 tab 的可见约束切分为展示分组:
 * disqualification 拆为"实质性(★)"与"一般废标"两组, ★组置顶;
 * 其余按 sub_type 分组。空组不产出(优雅降级)。
 */
export function buildClauseDisplayGroups(clauses: WritingClause[]): ClauseDisplayGroup[] {
  const byKey = new Map<string, WritingClause[]>()
  for (const clause of clauses) {
    let key: string
    if (isReferenceWritingClause(clause)) {
      key = WRITING_REFERENCE_GROUP_KEY
    } else if (isCheckOnlyWritingClause(clause)) {
      key = WRITING_CHECK_ONLY_GROUP_KEY
    } else if (clause.subType === 'disqualification' && isSubstantiveClause(clause)) {
      key = SUBSTANTIVE_GROUP_KEY
    } else if (isSuspectedAssessmentClause(clause)) {
      key = SCORING_ASSESSMENT_GROUP_KEY
    } else if (isUnscoredScoringClause(clause)) {
      key = SCORING_UNSCORED_GROUP_KEY
    } else {
      key = clause.subType
    }
    const bucket = byKey.get(key)
    if (bucket) {
      bucket.push(clause)
    } else {
      byKey.set(key, [clause])
    }
  }

  const groups: ClauseDisplayGroup[] = []
  const emitted = new Set<string>()
  for (const key of GROUP_DISPLAY_ORDER) {
    const items = byKey.get(key)
    if (items && items.length > 0) {
      groups.push(buildGroup(key, items))
      emitted.add(key)
    }
  }
  // 防御: 未纳入预定义顺序的未知 sub_type 兜底追加, 保证不丢条目
  for (const [key, items] of byKey) {
    if (!emitted.has(key) && items.length > 0) {
      groups.push(buildGroup(key, items))
    }
  }
  return groups
}

/** 收集组内未确认约束 id(供组级批量确认) */
export function collectGroupUnconfirmedIds(group: ClauseDisplayGroup): number[] {
  return group.clauses.filter((c) => c.confirmed === 0).map((c) => c.id)
}

/** 组内已确认数量 */
export function countGroupConfirmed(group: ClauseDisplayGroup): number {
  return group.clauses.filter((c) => c.confirmed === 1).length
}
