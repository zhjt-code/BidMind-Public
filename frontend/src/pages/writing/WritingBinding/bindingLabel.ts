/**
 * 单条 binding 的展示标签计算。
 *
 * 从 WritingBinding.tsx L43-75 整段迁出（行为 byte-equal）。
 */

import type { WritingBinding } from '@/services/writingApi'
import { getMaterialDisplayName, type MaterialDisplayLike } from '../materialDisplay.ts'
import { shortUserFacingClauseText, toUserFacingClauseText } from '../writingClauseDisplay.ts'
import type { BindingLabel } from './types'

export function getBindingLabel(
  b: WritingBinding,
  clauseMap: Map<number, { content: string; subType: string }>,
  materialMap: Map<number, MaterialDisplayLike>,
): BindingLabel {
  const clause = b.clauseId ? clauseMap.get(b.clauseId) : null
  const material = b.materialId ? materialMap.get(b.materialId) : null
  const reason = b.reason ? toUserFacingClauseText(b.reason) : null
  const materialName = getMaterialDisplayName(material)

  if (b.bindingType === 'clause_to_node' && clause) {
    const full = toUserFacingClauseText(clause.content)
    const display = shortUserFacingClauseText(clause.content, 50)
    return { tag: '约束', tagClass: 'bg-blue-100 text-blue-700', text: display, fullText: full, reason }
  }
  if (b.bindingType === 'material_to_node' && materialName) {
    return { tag: '素材', tagClass: 'bg-green-100 text-green-700', text: materialName, fullText: materialName, reason }
  }
  if (b.bindingType === 'material_to_clause') {
    const matText = materialName || `素材#${b.materialId}`
    const clauseFull = clause ? toUserFacingClauseText(clause.content) : `约束#${b.clauseId}`
    const clauseShort = clause ? shortUserFacingClauseText(clause.content, 30) : clauseFull
    const display = `${matText} -> ${clauseShort}`
    const full = `${matText} -> ${clauseFull}`
    return { tag: '素材->约束', tagClass: 'bg-purple-100 text-purple-700', text: display, fullText: full, reason }
  }

  const fallback = [
    b.clauseId && `约束#${b.clauseId}`,
    b.materialId && `素材#${b.materialId}`,
  ].filter(Boolean).join(' ')
  return { tag: '绑定', tagClass: 'bg-gray-100 text-gray-700', text: fallback || `#${b.id}`, fullText: fallback || `#${b.id}`, reason }
}
