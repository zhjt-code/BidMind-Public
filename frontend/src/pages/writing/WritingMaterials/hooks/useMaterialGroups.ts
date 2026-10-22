/**
 * 素材分组 memo hook。
 *
 * 抽自 WritingMaterials.tsx 的 rootMaterials / childMap / groupedChildMap 三组
 * useMemo。计算行为与原页面完全一致：
 *   - rootMaterials：parentMaterialId == null 的素材
 *   - childMap：parentMaterialId → 子素材数组，按类型/页码/id 三级排序
 *   - groupedChildMap：parentMaterialId → 5 类分组 bucket
 */

import { useMemo } from 'react'
import type { WritingMaterial } from '@/services/writingApi'
import {
  getChildGroupKey,
  getMaterialSortPage,
  getMaterialTypeOrder,
} from '../materialGrouping.ts'
import type { ChildGroupKey } from '../types'

export interface MaterialGroupsResult {
  rootMaterials: WritingMaterial[]
  childMap: Map<number, WritingMaterial[]>
  groupedChildMap: Map<number, Record<ChildGroupKey, WritingMaterial[]>>
}

export function useMaterialGroups(materials: WritingMaterial[]): MaterialGroupsResult {
  const rootMaterials = useMemo(
    () => materials.filter((m) => m.parentMaterialId == null),
    [materials],
  )

  const childMap = useMemo(() => {
    const grouped = new Map<number, WritingMaterial[]>()
    materials.forEach((material) => {
      if (material.parentMaterialId == null) return
      const existing = grouped.get(material.parentMaterialId) || []
      existing.push(material)
      grouped.set(material.parentMaterialId, existing)
    })
    grouped.forEach((items) =>
      items.sort((a, b) => {
        const typeOrder = getMaterialTypeOrder(a.materialType) - getMaterialTypeOrder(b.materialType)
        if (typeOrder !== 0) return typeOrder
        const pageOrder = getMaterialSortPage(a) - getMaterialSortPage(b)
        if (pageOrder !== 0) return pageOrder
        return a.id - b.id
      }),
    )
    return grouped
  }, [materials])

  const groupedChildMap = useMemo(() => {
    const grouped = new Map<number, Record<ChildGroupKey, WritingMaterial[]>>()
    childMap.forEach((children, parentId) => {
      const bucket: Record<ChildGroupKey, WritingMaterial[]> = {
        mixed: [],
        text: [],
        table: [],
        image: [],
        ignored: [],
      }
      children.forEach((child) => {
        bucket[getChildGroupKey(child)].push(child)
      })
      grouped.set(parentId, bucket)
    })
    return grouped
  }, [childMap])

  return { rootMaterials, childMap, groupedChildMap }
}
