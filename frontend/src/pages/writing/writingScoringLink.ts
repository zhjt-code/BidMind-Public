/**
 * 标书编写 -- 散落评分条目关联展示 helper。
 *
 * 后端 scoring_clause_link 为其它标签下与权威评分项等价的条目写入 metadata_json.scoring_ref，
 * 前端据此在约束卡片展示"已关联评分项"徽标，提示用户该条已与评分标准归属打通。
 */
import type { WritingClause } from '@/services/writingClauseTransforms'

export interface ScoringRef {
  clauseId: number | null
  scoreItemName: string
  maxScore: number | null
}

export function getScoringRef(clause: WritingClause): ScoringRef | null {
  const meta = clause.metadataJson
  if (!meta || typeof meta !== 'object') return null
  const ref = (meta as Record<string, unknown>).scoring_ref
  if (!ref || typeof ref !== 'object') return null
  const r = ref as Record<string, unknown>
  const name = typeof r.score_item_name === 'string' ? r.score_item_name : ''
  if (!name) return null
  return {
    clauseId: typeof r.clause_id === 'number' ? r.clause_id : null,
    scoreItemName: name,
    maxScore: typeof r.max_score === 'number' ? r.max_score : null,
  }
}

/** 生成"已关联评分项：名称（N分）"徽标文案；无关联返回 null。 */
export function getScoringRefLabel(clause: WritingClause): string | null {
  const ref = getScoringRef(clause)
  if (!ref) return null
  const scorePart = ref.maxScore != null ? `（${ref.maxScore}分）` : ''
  return `已关联评分项：${ref.scoreItemName}${scorePart}`
}
