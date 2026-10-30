/**
 * 评分项满分修正入口。
 *
 * 职责: 在评分项/重点评审行下方展示满分及人工修正入口（onUpdateScoringWeight 未提供时不渲染）。
 * 不做: 不重复展示评分项名/卷包/规则原文（正文已在 RequirementTable 中展示）；不发 API（由调用方回调）。
 */
import React, { useState } from 'react'
import type { Requirement } from '../../requirementReviewTransforms'

interface ScoringItemDetailProps {
  requirement: Requirement
  /** 满分修正回调；未提供时不展示编辑入口。 */
  onUpdateScoringWeight?: (id: number, weight: number) => Promise<void>
}

export const ScoringItemDetail: React.FC<ScoringItemDetailProps> = ({
  requirement,
  onUpdateScoringWeight,
}) => {
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState<string>('')

  if (!onUpdateScoringWeight) return null

  const handleEditStart = () => {
    setEditValue(
      requirement.scoreWeight != null ? String(requirement.scoreWeight) : '',
    )
    setEditing(true)
  }

  const handleEditSubmit = async () => {
    const parsed = parseFloat(editValue)
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setEditing(false)
      return
    }
    if (parsed === requirement.scoreWeight) {
      setEditing(false)
      return
    }
    setEditing(false)
    await onUpdateScoringWeight(requirement.id, parsed)
  }

  return (
    <div className="mt-1.5 text-xs text-gray-500">
      <div className="mt-1 flex items-center gap-1.5">
        <span className="text-gray-400">满分:</span>
        {editing ? (
          <>
            <input
              type="number"
              min={0.01}
              step={0.5}
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleEditSubmit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleEditSubmit()
                if (e.key === 'Escape') setEditing(false)
              }}
              className="w-16 border rounded px-1 py-0.5 text-xs focus:outline-hidden focus:ring-1 focus:ring-primary"
              autoFocus
              aria-label="修正满分分值"
            />
            <span className="text-gray-400">分</span>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-gray-400 hover:text-gray-600 text-xs"
            >
              取消
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={handleEditStart}
            className="text-blue-500 hover:text-blue-700 underline-offset-2 hover:underline"
            title="修正此评分项满分"
          >
            {requirement.scoreWeight}分
            <span className="ml-0.5 text-gray-400">[修改]</span>
          </button>
        )}
      </div>
    </div>
  )
}

