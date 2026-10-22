/**
 * 招标要求列表 CRUD + 乐观更新 hook。
 *
 * 从原 RequirementReview.tsx 抽出 4 个核心操作:
 * - 切换否决项 (toggleMandatory)
 * - 更新描述 (updateDescription)
 * - 删除一条 (deleteRequirement)
 * - 手动新增 (createRequirement)
 *
 * 所有变更都遵循"先乐观更新本地 state → 后发 PUT/DELETE/POST → 失败回滚"模式,
 * 与原代码 byte-equal 兼容。
 *
 * 设计取舍 (与 R2-10 plan §七.5 一致):
 * - editingId / deletingId / showAddModal 等"UI 控制态" **不在 hook 内**,
 * 由主页面 + 子组件持有,本 hook 仅暴露纯 list 操作
 * - setRequirements 也对外暴露,供 useRequirementExtractionPolling 的
 * onRequirementsLoaded 回调直接回填
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { requirementService } from '@/services/requirementApi'
import type { Requirement } from '../../requirementReviewTransforms'
import { transformRequirement } from '../../requirementReviewTransforms'

export interface UseRequirementListOptions {
  projectId: string | undefined
}

export interface UseRequirementListReturn {
  requirements: Requirement[]
  setRequirements: React.Dispatch<React.SetStateAction<Requirement[]>>
  /** 切换某条要求的否决项标记（含乐观更新 + 失败回滚 + toast 提示）。 */
  toggleMandatory: (id: number) => Promise<void>
/**
 * 更新某条要求的描述文案。
 * 若新旧值相同或未找到目标，不发请求；调用方应自行管理 editingId 退出态。
 */
  updateDescription: (id: number, description: string) => Promise<void>
  /** 实际删除一条要求（不含确认弹窗逻辑；调用方自管 deletingId）。 */
  deleteRequirement: (id: number) => Promise<void>
  /** 手动新增一条要求；成功时追加到列表尾部，失败返回 null 并 toast 提示。 */
  createRequirement: (data: {
    category: string
    description: string
    is_mandatory: boolean
  }) => Promise<Requirement | null>
  /** 手动补录一条评分项；成功时追加到列表尾部，失败返回 null 并 toast 提示。 */
  createScoringItem: (data: {
    category: string
    score_item_name: string
    description: string
    score_weight: number
    score_rules: string[]
    deduction_rules: string[]
    volume?: string | null
    volume_label?: string | null
    lot?: string | null
    source_pages: number[]
  }) => Promise<Requirement | null>
  /** 把折叠的参考条款"升为审查项"（review_relevance=actionable，含乐观更新 + 整组 fan-out）。 */
  promoteToActionable: (id: number) => Promise<void>
/**
 * 把主列表要求"降为参考条款"（review_relevance=informational， P2.3）。
 * 评分项/重点评审项直接 no-op（评分项铁律：必须参与审查，不可降级）。
 */
  demoteToReference: (id: number) => Promise<void>
/**
 * 修正评分项满分分值（P2 C1）。
 * 仅 scoring_item 调用；weight <= 0 时 no-op；与当前值相同时 no-op。
 */
  updateScoringWeight: (id: number, weight: number) => Promise<void>
}

export function useRequirementList(
  options: UseRequirementListOptions,
): UseRequirementListReturn {
  const { projectId } = options
  const [requirements, setRequirements] = useState<Requirement[]>([])

  const toggleMandatory = useCallback(
    async (id: number) => {
      if (!projectId) return
      const req = requirements.find((r) => r.id === id)
      if (!req) return

      // 展示分组下，否决项切换需作用于整组底层行（复合拆分子面），
      // 否则未同步的子面会在下次加载时以旧值重新分裂出来。
      const targetIds = req.memberIds.length > 0 ? req.memberIds : [id]
      const nextMandatory = !req.isMandatory

      setRequirements((prev) =>
        prev.map((r) => (r.id === id ? { ...r, isMandatory: nextMandatory } : r)),
      )

      try {
        await Promise.all(
          targetIds.map((memberId) =>
            requirementService.updateRequirement(parseInt(projectId), memberId, {
              is_mandatory: nextMandatory,
            }),
          ),
        )
      } catch (err) {
        setRequirements((prev) =>
          prev.map((r) => (r.id === id ? { ...r, isMandatory: req.isMandatory } : r)),
        )
        toast.error(`更新失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  const updateDescription = useCallback(
    async (id: number, description: string) => {
      if (!projectId) return
      const req = requirements.find((r) => r.id === id)
      if (!req || req.description === description) return

      // 描述更新必须同步整组底层行，否则子面描述变得不一致，
      // 下次加载将无法再折叠为一条，视觉重复复活。
      const targetIds = req.memberIds.length > 0 ? req.memberIds : [id]

      setRequirements((prev) =>
        prev.map((r) => (r.id === id ? { ...r, description } : r)),
      )

      try {
        await Promise.all(
          targetIds.map((memberId) =>
            requirementService.updateRequirement(parseInt(projectId), memberId, {
              description,
            }),
          ),
        )
      } catch (err) {
        setRequirements((prev) =>
          prev.map((r) => (r.id === id ? { ...r, description: req.description } : r)),
        )
        toast.error(`更新失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  const deleteRequirement = useCallback(
    async (id: number) => {
      if (!projectId) return
      const deletedReq = requirements.find((r) => r.id === id)

      // 删除作用于整组底层行，否则残留子面仍喂给下游审查并在刷新后重现。
      const targetIds = deletedReq && deletedReq.memberIds.length > 0 ? deletedReq.memberIds : [id]

      setRequirements((prev) => prev.filter((r) => r.id !== id))

      try {
        await Promise.all(
          targetIds.map((memberId) =>
            requirementService.deleteRequirement(parseInt(projectId), memberId),
          ),
        )
      } catch (err) {
        if (deletedReq) {
          setRequirements((prev) => [...prev, deletedReq])
        }
        toast.error(`删除失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  const createRequirement = useCallback(
    async (data: { category: string; description: string; is_mandatory: boolean }) => {
      if (!projectId) return null

      try {
        const response = await requirementService.createRequirement(
          parseInt(projectId),
          data,
        )
        const created = (response as { data?: Record<string, unknown> }).data
        if (!created) return null
        const transformed = transformRequirement(created)
        setRequirements((prev) => [...prev, transformed])
        return transformed
      } catch (err) {
        toast.error(`添加失败: ${err instanceof Error ? err.message : '未知错误'}`)
        return null
      }
    },
    [projectId],
  )

  const createScoringItem = useCallback(
    async (data: {
      category: string
      score_item_name: string
      description: string
      score_weight: number
      score_rules: string[]
      deduction_rules: string[]
      volume?: string | null
      volume_label?: string | null
      lot?: string | null
      source_pages: number[]
    }) => {
      if (!projectId) return null

      try {
        const response = await requirementService.createScoringItem(
          parseInt(projectId),
          data,
        )
        const created = (response as { data?: Record<string, unknown> }).data
        if (!created) return null
        const transformed = transformRequirement(created)
        setRequirements((prev) => [...prev, transformed])
        return transformed
      } catch (err) {
        toast.error(`评分项添加失败: ${err instanceof Error ? err.message : '未知错误'}`)
        return null
      }
    },
    [projectId],
  )

  const promoteToActionable = useCallback(
    async (id: number) => {
      if (!projectId) return
      const req = requirements.find((r) => r.id === id)
      if (!req || req.reviewRelevance === 'actionable') return

      // 整组 fan-out，避免子面残留旧级别在下次加载时重新折叠（与 同理）。
      const targetIds = req.memberIds.length > 0 ? req.memberIds : [id]
      const prevRelevance = req.reviewRelevance

      setRequirements((prev) =>
        prev.map((r) => (r.id === id ? { ...r, reviewRelevance: 'actionable' } : r)),
      )

      try {
        await Promise.all(
          targetIds.map((memberId) =>
            requirementService.updateRequirement(parseInt(projectId), memberId, {
              review_relevance: 'actionable',
            }),
          ),
        )
      } catch (err) {
        setRequirements((prev) =>
          prev.map((r) => (r.id === id ? { ...r, reviewRelevance: prevRelevance } : r)),
        )
        toast.error(`升为审查项失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  const demoteToReference = useCallback(
    async (id: number) => {
      if (!projectId) return
      const req = requirements.find((r) => r.id === id)
      if (!req || req.reviewRelevance !== 'actionable') return
      // 评分项铁律: 评分项/重点评审项必须参与审查, 禁止降级
      if (req.requirementKind === 'scoring_item' || req.isKeyReview) return

      // 整组 fan-out，避免子面残留 actionable 在下次加载时被"最可见级别"拉回主列表。
      const targetIds = req.memberIds.length > 0 ? req.memberIds : [id]

      setRequirements((prev) =>
        prev.map((r) =>
          r.id === id
            ? { ...r, reviewRelevance: 'informational', relevanceReason: 'manual' }
            : r,
        ),
      )

      try {
        await Promise.all(
          targetIds.map((memberId) =>
            requirementService.updateRequirement(parseInt(projectId), memberId, {
              review_relevance: 'informational',
            }),
          ),
        )
      } catch (err) {
        setRequirements((prev) =>
          prev.map((r) =>
            r.id === id
              ? { ...r, reviewRelevance: 'actionable', relevanceReason: req.relevanceReason }
              : r,
          ),
        )
        toast.error(`降为参考条款失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  const updateScoringWeight = useCallback(
    async (id: number, weight: number) => {
      if (!projectId) return
      if (!Number.isFinite(weight) || weight <= 0) return
      const req = requirements.find((r) => r.id === id)
      if (!req || req.scoreWeight === weight) return

      const prevWeight = req.scoreWeight
      setRequirements((prev) =>
        prev.map((r) => (r.id === id ? { ...r, scoreWeight: weight } : r)),
      )

      try {
        await requirementService.updateRequirement(parseInt(projectId), id, {
          score_weight: weight,
        })
      } catch (err) {
        setRequirements((prev) =>
          prev.map((r) => (r.id === id ? { ...r, scoreWeight: prevWeight } : r)),
        )
        toast.error(`满分修正失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    },
    [projectId, requirements],
  )

  return {
    requirements,
    setRequirements,
    toggleMandatory,
    updateDescription,
    deleteRequirement,
    createRequirement,
    createScoringItem,
    promoteToActionable,
    demoteToReference,
    updateScoringWeight,
  }
}
