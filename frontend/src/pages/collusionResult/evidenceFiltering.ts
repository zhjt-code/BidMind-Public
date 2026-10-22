/**
 * 职责: 串标证据等级的筛选与计数纯函数。
 * 不做: 不处理风险等级筛选,不持有状态,不依赖 React/store/API。
 *
 * 设计要点:
 * - finding 模式采用"严格等级"语义: 一条 finding 自身的 level 与筛选项 level 严格相等。
 * - pair 模式采用"包含语义": 一个 pair 至少含一条 X 级 finding 即视为含 X 级证据,
 * 同一 pair 可同时属于多档 (与 UI label "含 X 级证据" 自洽)。
 *
 * 业务背景: 项目规则《collusion.mdc - 二、三级证据体系》定义 D1-D8 维度的一/二/三级
 * 为互斥分类,UI 筛选必须与该分类语义一致。历史实现误用"≥某级"阈值语义导致 。
 */

export type EvidenceLevel = 'first' | 'second' | 'third'
export type EvidenceLevelFilter = EvidenceLevel | 'all'

const EVIDENCE_LEVELS: readonly EvidenceLevel[] = ['first', 'second', 'third'] as const

export interface LeveledFinding {
  level: string
}

export interface FindingContainer {
  findings: LeveledFinding[]
}

export interface EvidenceLevelCounts {
  all: number
  first: number
  second: number
  third: number
}

const emptyCounts = (total: number): EvidenceLevelCounts => ({
  all: total,
  first: 0,
  second: 0,
  third: 0,
})

/** 按等级独立计数 finding,不做累加 (first 不计入 second/third)。 */
export function countFindingsByLevel<F extends LeveledFinding>(
  findings: readonly F[],
): EvidenceLevelCounts {
  const counts = emptyCounts(findings.length)
  for (const f of findings) {
    if (f.level === 'first') counts.first += 1
    else if (f.level === 'second') counts.second += 1
    else if (f.level === 'third') counts.third += 1
  }
  return counts
}

/** 严格按 level== 匹配,'all' 直通。unknown/其他值在非 'all' 筛选下一律被过滤。 */
export function filterFindingsByLevel<F extends LeveledFinding>(
  findings: readonly F[],
  level: EvidenceLevelFilter,
): F[] {
  if (level === 'all') return [...findings]
  return findings.filter(f => f.level === level)
}

/** pair 是否至少含一条给定等级的 finding;'all' 直通返回 true。 */
export function pairContainsEvidenceLevel(
  pair: FindingContainer,
  level: EvidenceLevelFilter,
): boolean {
  if (level === 'all') return true
  if (!pair.findings || pair.findings.length === 0) return false
  return pair.findings.some(f => f.level === level)
}

/**
 * 按"包含语义"统计 pair 数: 一个 pair 含 first+third 时, first 与 third 各计 1,
 * second 计 0; 同一 pair 可同时计入多档。
 */
export function countPairsByEvidenceLevel<P extends FindingContainer>(
  pairs: readonly P[],
): EvidenceLevelCounts {
  const counts = emptyCounts(pairs.length)
  for (const pair of pairs) {
    for (const level of EVIDENCE_LEVELS) {
      if (pairContainsEvidenceLevel(pair, level)) {
        counts[level] += 1
      }
    }
  }
  return counts
}
