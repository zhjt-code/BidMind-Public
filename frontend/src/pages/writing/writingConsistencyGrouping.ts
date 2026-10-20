export interface ConsistencyIssueLike {
  type?: string | null
  severity?: string | null
  message?: string | null
  detail?: string | null
  nodeId?: number | null
}

export interface ConsistencyIssueGroup<T extends ConsistencyIssueLike = ConsistencyIssueLike> {
  key: string
  title: string
  description: string
  severity: string
  issues: T[]
  collapsedByDefault: boolean
}

interface GroupDefinition {
  key: string
  title: string
  description: string
  collapsedWhenNonHigh: boolean
  match: (issueType: string) => boolean
}

const GROUP_DEFINITIONS: GroupDefinition[] = [
  {
    key: 'fixed_template',
    title: '固定模板一致性',
    description: '投标函、承诺书、授权书等固定格式需与招标原模板保持一致。',
    collapsedWhenNonHigh: false,
    match: (issueType) => issueType.startsWith('fixed_template'),
  },
  {
    key: 'clause_coverage',
    title: '已确认约束未被正文来源覆盖',
    description: '已确认的写作约束没有进入章节草稿的来源记录，需核对是否漏写。',
    collapsedWhenNonHigh: true,
    match: (issueType) => issueType === 'clause_coverage',
  },
  {
    key: 'missing_content',
    title: '必填章节缺失',
    description: '目录中的必填章节尚未生成有效正文。',
    collapsedWhenNonHigh: false,
    match: (issueType) => issueType === 'missing_content',
  },
  {
    key: 'disqualification_risk',
    title: '废标风险',
    description: '正文中可能存在与废标条款冲突的表达。',
    collapsedWhenNonHigh: false,
    match: (issueType) => issueType === 'disqualification_risk',
  },
  {
    key: 'scoring_response',
    title: '评分项响应',
    description: '评分项是否被正文有效响应，直接影响投标得分；价格分等非正文响应项仅作提示。',
    collapsedWhenNonHigh: false,
    match: (issueType) => issueType.startsWith('scoring_'),
  },
  {
    key: 'cross_reference',
    title: '交叉引用问题',
    description: '章节引用、附件引用或降级检查需要人工复核。',
    collapsedWhenNonHigh: true,
    match: (issueType) => issueType.startsWith('cross_reference'),
  },
  {
    key: 'terminology',
    title: '术语一致性',
    description: '同一角色或实体出现了多种称谓，建议全文统一。',
    collapsedWhenNonHigh: true,
    match: (issueType) => issueType === 'terminology_inconsistency',
  },
]

const OTHER_GROUP: GroupDefinition = {
  key: 'other',
  title: '其他检查提醒',
  description: '暂未归入固定业务类别的检查项。',
  collapsedWhenNonHigh: true,
  match: () => true,
}

const SEVERITY_RANK: Record<string, number> = {
  high: 4,
  medium: 3,
  warning: 2,
  low: 1,
  info: 0,
}

export function buildConsistencyIssueGroups<T extends ConsistencyIssueLike>(
  issues: readonly T[] | null | undefined,
): ConsistencyIssueGroup<T>[] {
  if (!issues || issues.length === 0) return []

  const buckets = new Map<string, { definition: GroupDefinition; issues: T[] }>()
  for (const issue of issues) {
    const definition = resolveGroupDefinition(issue?.type)
    const existing = buckets.get(definition.key)
    if (existing) {
      existing.issues.push(issue)
    } else {
      buckets.set(definition.key, { definition, issues: [issue] })
    }
  }

  return [...GROUP_DEFINITIONS, OTHER_GROUP]
    .map((definition) => buckets.get(definition.key))
    .filter((bucket): bucket is { definition: GroupDefinition; issues: T[] } => Boolean(bucket))
    .map(({ definition, issues: groupIssues }) => {
      const severity = getHighestSeverity(groupIssues)
      return {
        key: definition.key,
        title: definition.title,
        description: definition.description,
        severity,
        issues: groupIssues,
        collapsedByDefault: severity !== 'high' && definition.collapsedWhenNonHigh,
      }
    })
}

export function getConsistencyIssueGroupStats(
  groups: readonly ConsistencyIssueGroup[],
): { groupCount: number; highGroupCount: number } {
  return {
    groupCount: groups.length,
    highGroupCount: groups.filter((group) => group.severity === 'high').length,
  }
}

function resolveGroupDefinition(issueType: string | null | undefined): GroupDefinition {
  const normalizedType = String(issueType || '').trim()
  return GROUP_DEFINITIONS.find((definition) => definition.match(normalizedType)) ?? OTHER_GROUP
}

function getHighestSeverity(issues: readonly ConsistencyIssueLike[]): string {
  let highest = 'low'
  let highestRank = -1
  for (const issue of issues) {
    const severity = String(issue?.severity || 'low')
    const rank = SEVERITY_RANK[severity] ?? -1
    if (rank > highestRank) {
      highest = severity
      highestRank = rank
    }
  }
  return highest
}
