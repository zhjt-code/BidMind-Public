/**
 * 结果页"评分项视图"纯函数模块。
 *
 * 职责: 收口评分项过滤、按原文评分卷单轴分组导航/过滤/排序。
 * 不做: 不做 UI 渲染；不重算预估总分与卷内得分（一律复用后端权威 summary 聚合，
 * 规避 前端二次加权口径漂移；护栏测试锁定本模块不出现单条得分字段）。
 *
 * 轴变更说明（2026-07-29）: 换轴 ADR 曾放弃分卷导航, 依据是"项级卷归属在人工真值层
 * 不可复核"。 用结构判据(分组列列结构同质 + 父子分值闭合)取代词面推断后
 * 该依据不再成立, 故恢复卷轴。两点约束:
 * 1. 轴源只能是原文组标签(volume_label), 不得回到被 判定为缺陷的六类枚举;
 * 2. 卷轴为唯一分组轴——分类轴经实测判定为交互过载(评分卷/分类/符合程度/风险共四
 * 个维度)已移除, 分类信息降级为条目徽标, 零命中的 fail-visible 由
 * isCategoryInferenceMiss 在徽标上直接显式化。
 */
import type { ReviewResult, VolumeScore } from './types.ts'
import { volumeLabel } from '../requirementReviewTransforms.ts'

/** 单条评审项是否属于"评分项满分全集"（严格对齐后端 is_scoring_universe_member 口径）。 */
export function isScoringUniverseMember(r: ReviewResult): boolean {
  if (r.requirementKind === 'scoring_item') return true
  return Boolean(r.isKeyReview) && (Number(r.maxScore) || 0) > 0
}

/** 过滤出评分项全集。 */
export function filterScoringResults(results: ReviewResult[]): ReviewResult[] {
  return results.filter(isScoringUniverseMember)
}

// ==================== 逐档判定（右栏评分明细消费）====================

/** 单条逐档判定结果（对应后端 RuleAssessment schema）。 */
export interface RuleAssessment {
  rule_excerpt?: string
  status?: 'met' | 'partial' | 'not' | 'unknown' | string
  points?: number | null
  reason?: string
  evidence_pages?: number[]
}

/**
 * 分类零命中标记（categoryBasis 显式为 null）：卡片分类徽标据此直接显示"未分类"，
 * 而非显示一个未经关键词验证的推断分类名。
 *
 * 单轴化后左栏不再有"未分类"桶，该 fail-visible 下移到条目徽标——更贴近条目，
 * 且无需悬停 title 即可看见（旧实现零命中时徽标仍显示推断分类名）。
 * 字段缺失（旧数据无 category_basis）不算零命中，按既有 category 正常展示。
 */
export const UNCATEGORIZED_CATEGORY_LABEL = '未分类'

export function isCategoryInferenceMiss(r: ReviewResult): boolean {
  return r.categoryBasis === null
}

// ==================== 评分项视图评分卷桶（原文组标签轴）====================

/**
 * 采购包缺失时的展示标签 / "包·卷"复合键分隔符。
 *
 * 二者与后端 review_contracts 的 UNASSIGNED_LOT_LABEL、LOT_VOLUME_SEPARATOR 是
 * 同字面契约——后端桶键由这两个常量拼出，前端必须逐字对齐才能让导航键与明细行
 * 过滤键落到同一桶（: 桶键与过滤键错位会导致点击筛选后列表为空）。
 */
export const UNASSIGNED_LOT_LABEL = '未识别归属'
export const LOT_VOLUME_SEPARATOR = '·'

/** 卷标签自带的满分括注（如"磋商报价（20分）"）；导航已单列分值，展示时须去重。 */
const VOLUME_LABEL_SCORE_SUFFIX_RE = /[（(]\s*\d+(?:\.\d+)?\s*分\s*[)）]\s*$/

/**
 * 同粗卷唯一原文标签映射（复现后端 _unique_volume_labels）。
 *
 * 仅当某粗卷下所有条目的原文标签唯一时才可继承给同卷缺标签的行；标签有分歧时
 * 不继承（否则首个标签会冒名整卷，即 的展示分组坍缩）。
 */
export function buildInheritedVolumeLabels(
  scoringResults: ReviewResult[],
): Record<string, string> {
  const labelsByVolume = new Map<string, Set<string>>()
  for (const r of scoringResults) {
    const volumeKey = String(r.volume || 'unspecified')
    const label = String(r.volumeLabel || '').trim()
    if (!label) continue
    const bucket = labelsByVolume.get(volumeKey) || new Set<string>()
    bucket.add(label)
    labelsByVolume.set(volumeKey, bucket)
  }
  const inherited: Record<string, string> = {}
  for (const [volumeKey, labels] of labelsByVolume) {
    if (labels.size === 1) inherited[volumeKey] = labels.values().next().value as string
  }
  return inherited
}

/**
 * 创建评分卷桶键解析器（严格复现后端 _aggregate_scoring_scope 的桶键构造）:
 * 自带原文标签 > 同卷唯一继承标签 > 标准卷名映射; 多包文档前缀"包·"。
 *
 * 返回闭包而非导出裸函数，是因为"同卷唯一继承"依赖全集扫描结果，逐条调用时
 * 必须共享同一份映射，避免调用方各自构造导致口径分裂。
 */
export function createScoringVolumeKeyResolver(
  scoringResults: ReviewResult[],
  multiLot: boolean,
): (r: ReviewResult) => string {
  const inherited = buildInheritedVolumeLabels(scoringResults)
  return (r: ReviewResult): string => {
    const volumeKey = String(r.volume || 'unspecified')
    const ownLabel = String(r.volumeLabel || '').trim()
    const label = ownLabel || inherited[volumeKey] || volumeLabel(volumeKey)
    if (!multiLot) return label
    const lot = String(r.lot || '').trim() || UNASSIGNED_LOT_LABEL
    return `${lot}${LOT_VOLUME_SEPARATOR}${label}`
  }
}

/** 桶键 → 展示文案：剥离自带满分括注，剥空则回退原键。 */
export function formatScoringVolumeLabel(key: string): string {
  const stripped = key.replace(VOLUME_LABEL_SCORE_SUFFIX_RE, '').trim()
  return stripped || key
}

/** 左栏"评分卷"导航项（分值一律取自后端权威卷桶，不前端累加）。 */
export interface ScoringVolumeNavItem {
  /** 过滤匹配键：与后端卷桶键、明细行解析键三者同一。 */
  key: string
  /** 展示文案：去满分括注后的卷名。 */
  label: string
  /** 该卷评分项数量（前端按解析键计，与筛选结果一致）。 */
  count: number
  /** 该卷已得分；无预估产出时为 null（展示为破折号，不得显示 0 误导为评了0分）。 */
  earned: number | null
  /** 该卷满分。 */
  max: number
}

/**
 * 评分卷权威展示序：后端卷桶键序（即文档内出现顺序）优先，后端未含的桶按前端
 * 首见序排其后。左栏导航与中栏列表共用本序，保证三栏同序。
 */
export function buildScoringVolumeOrder(
  scoringResults: ReviewResult[],
  volumeScoreKeys: string[],
  resolveKey: (r: ReviewResult) => string,
): string[] {
  const firstSeen: string[] = []
  for (const r of scoringResults) {
    const key = resolveKey(r)
    if (!firstSeen.includes(key)) firstSeen.push(key)
  }
  return firstSeen.slice().sort((a, b) => {
    const ia = volumeScoreKeys.indexOf(a)
    const ib = volumeScoreKeys.indexOf(b)
    if (ia !== ib) {
      return (ia < 0 ? Number.MAX_SAFE_INTEGER : ia) - (ib < 0 ? Number.MAX_SAFE_INTEGER : ib)
    }
    return firstSeen.indexOf(a) - firstSeen.indexOf(b)
  })
}

/**
 * 构造评分卷导航项：桶集合以前端解析键为准（保证点击必有结果），得分与满分按同名
 * 键取自后端权威卷桶。后端无对应桶时满分记 0 而不隐藏该桶——隐藏等于静默丢项，
 * 显示 0 反而暴露两侧键不一致，便于发现口径漂移。
 */
export function buildScoringVolumeNavItems(
  scoringResults: ReviewResult[],
  volumeScores: Record<string, VolumeScore>,
  resolveKey: (r: ReviewResult) => string,
  options: { hasEstimate: boolean },
): ScoringVolumeNavItem[] {
  const counts = new Map<string, number>()
  for (const r of scoringResults) {
    const key = resolveKey(r)
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return buildScoringVolumeOrder(scoringResults, Object.keys(volumeScores), resolveKey).map(
    (key) => {
      const bucket = volumeScores[key]
      return {
        key,
        label: formatScoringVolumeLabel(key),
        count: counts.get(key) || 0,
        earned: options.hasEstimate && bucket ? bucket.estimated : null,
        max: bucket ? bucket.max : 0,
      }
    },
  )
}

/** 评分项视图按评分卷桶过滤：volumeKey 为 null 时不过滤（=全部评分项）。 */
export function filterByScoringVolume(
  results: ReviewResult[],
  volumeKey: string | null,
  resolveKey: (r: ReviewResult) => string,
): ReviewResult[] {
  if (volumeKey === null) return results
  return results.filter((r) => resolveKey(r) === volumeKey)
}

function firstSourcePage(r: ReviewResult): number {
  const pages = r.requirementSourcePages
  return pages && pages.length > 0 ? pages[0] : Number.POSITIVE_INFINITY
}

/**
 * 评分项视图中栏列表权威排序：评分卷序（同左栏导航）→ 来源页 → id。
 *
 * 排序轴必须与左栏导航轴一致（三栏同序）：右栏详情跟随中栏选中项，故
 * 只要左中同序，三栏即同序。卷序取自 buildScoringVolumeOrder，不在此另立规则。
 */
export function sortScoringResultsByVolume(
  results: ReviewResult[],
  resolveKey: (r: ReviewResult) => string,
  volumeOrder: string[],
): ReviewResult[] {
  const orderOf = (r: ReviewResult): number => {
    const index = volumeOrder.indexOf(resolveKey(r))
    return index < 0 ? Number.MAX_SAFE_INTEGER : index
  }
  return [...results].sort((a, b) => {
    const orderDiff = orderOf(a) - orderOf(b)
    if (orderDiff !== 0) return orderDiff
    const pageDiff = firstSourcePage(a) - firstSourcePage(b)
    if (pageDiff !== 0) return pageDiff
    return a.id - b.id
  })
}
