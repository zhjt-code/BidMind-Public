// 约束消费覆盖诊断的类型与 snake_case(后端) -> camelCase 转换。
// 独立成纯文件以便 node --test 契约测试(避免 import 整个 writingApi 触发 Vite 运行时依赖)。

export interface ClauseCoverageSample {
  id: number
  summary: string
}

export interface ClauseCoverageReasonGroup {
  count: number
  samples: ClauseCoverageSample[]
}

export interface ClauseCoverageSubTypeStats {
  counted: number
  covered: number
  uncovered: number
  reference: number
  checkOnly: number
}

// 全局分母/已覆盖 + 未覆盖三类可行动原因(待人工分配/未匹配/待生成)
export interface ClauseCoverage {
  counted: number
  covered: number
  uncovered: number
  pendingAssignment: ClauseCoverageReasonGroup
  unmatched: ClauseCoverageReasonGroup
  pendingGeneration: ClauseCoverageReasonGroup
  bySubType: Record<string, ClauseCoverageSubTypeStats>
}

function toGroup(value: unknown): ClauseCoverageReasonGroup {
  const group = (value ?? {}) as Record<string, unknown>
  const samples = Array.isArray(group.samples)
    ? (group.samples as Record<string, unknown>[])
    : []
  return {
    count: (group.count as number) ?? 0,
    samples: samples.map((sample) => ({
      id: sample.id as number,
      summary: (sample.summary as string) ?? '',
    })),
  }
}

function toSubTypeStats(value: unknown): ClauseCoverageSubTypeStats {
  const stats = (value ?? {}) as Record<string, unknown>
  return {
    counted: (stats.counted as number) ?? 0,
    covered: (stats.covered as number) ?? 0,
    uncovered: (stats.uncovered as number) ?? 0,
    reference: (stats.reference as number) ?? 0,
    checkOnly: (stats.check_only as number) ?? 0,
  }
}

function toBySubType(value: unknown): Record<string, ClauseCoverageSubTypeStats> {
  if (!value || typeof value !== 'object') return {}
  const result: Record<string, ClauseCoverageSubTypeStats> = {}
  for (const [subType, rawStats] of Object.entries(value as Record<string, unknown>)) {
    result[subType] = toSubTypeStats(rawStats)
  }
  return result
}

export function transformClauseCoverage(raw: Record<string, unknown>): ClauseCoverage {
  return {
    counted: (raw.counted as number) ?? 0,
    covered: (raw.covered as number) ?? 0,
    uncovered: (raw.uncovered as number) ?? 0,
    pendingAssignment: toGroup(raw.pending_assignment),
    unmatched: toGroup(raw.unmatched),
    pendingGeneration: toGroup(raw.pending_generation),
    bySubType: toBySubType(raw.by_sub_type),
  }
}
