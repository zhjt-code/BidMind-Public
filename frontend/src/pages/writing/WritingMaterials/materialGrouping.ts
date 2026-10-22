/**
 * 素材分组、排序、徽章纯函数。
 *
 * 全部为无副作用纯函数，便于 node:test 单测覆盖。
 * 行为契约与原 WritingMaterials.tsx 行为完全一致。
 */

import type { WritingMaterial, WritingMaterialImageAsset } from '@/services/writingApi'
import { formatPageRangeLabel } from '../../../services/sourcePageLabels.ts'
import { MATERIAL_TYPE_LABELS } from './constants.ts'
import type { ChildGroupKey } from './types'

/**
 * 格式化素材页码范围：
 * - 有 sourcePageStart：返回 `P{start}-P{end}`（缺 end 则单页）
 * - 仅有 sourcePage：返回 `P{n}`
 * - 都缺：返回 null
 */
export function formatPageRange(material: WritingMaterial): string | null {
  if (material.sourcePageStart != null) {
    return formatPageRangeLabel(material.sourcePageStart, material.sourcePageEnd) || null
  }
  if (material.sourcePage != null) {
    return formatPageRangeLabel(material.sourcePage) || null
  }
  return null
}

/** 素材类型 → 中文标签；未知类型回退为中文占位，避免裸出内部枚举 */
export function formatMaterialTypeLabel(material: WritingMaterial): string {
  return MATERIAL_TYPE_LABELS[material.materialType] || '待识别素材'
}

/** 素材类型排序权重（text<table<image<其它） */
export function getMaterialTypeOrder(materialType: string): number {
  if (materialType === 'text') return 0
  if (materialType === 'table') return 1
  if (materialType === 'image') return 2
  return 3
}

/** 取素材用于排序的页码（缺则按最大值排到最后） */
export function getMaterialSortPage(material: WritingMaterial): number {
  return material.sourcePageStart ?? material.sourcePage ?? Number.MAX_SAFE_INTEGER
}

/**
 * 计算子素材所属分组：
 * - splitReviewStatus='ignored' → 'ignored'（优先）
 * - materialType='table' → 'table'
 * - materialType='image' → 'image'
 * - imageCount>0 → 'mixed'（图文混合）
 * - 其它 → 'text'
 */
export function getChildGroupKey(material: WritingMaterial): ChildGroupKey {
  if (material.splitReviewStatus === 'ignored') return 'ignored'
  if (material.materialType === 'table') return 'table'
  if (material.materialType === 'image') return 'image'
  if (material.imageCount > 0) return 'mixed'
  return 'text'
}

/**
 * 计算素材"主要复核状态"徽章：
 * - ignored → 已忽略
 * - reviewImageCount>0 → 图片待复核 N 张
 * - splitReviewStatus='needs_review' → 待确认
 * - 其它 → null（不显示徽章）
 */
export function getPrimaryReviewBadge(
  material: WritingMaterial,
): { label: string; className: string } | null {
  if (material.splitReviewStatus === 'ignored') {
    return { label: '已忽略', className: 'border-slate-300 text-slate-700' }
  }
  if (material.reviewImageCount > 0) {
    return {
      label: `图片待复核 ${material.reviewImageCount} 张`,
      className: 'border-amber-300 text-amber-700',
    }
  }
  if (material.splitReviewStatus === 'needs_review') {
    return { label: '待确认', className: 'border-amber-300 text-amber-700' }
  }
  return null
}

const IMAGE_TYPE_LABELS: Record<string, string> = {
  certificate: '证照',
  document: '文档截图',
  photo: '照片',
  chart: '图表',
}

const BUSINESS_SUBTYPE_LABELS: Record<string, string> = {
  business_license: '营业执照',
  qualification_certificate: '资质证书',
  safety_license: '安全生产许可证',
  id_card: '身份证明',
  contract: '合同',
}

function formatAssetLabel(value: string | null | undefined, labels: Record<string, string>): string | null {
  if (!value) return null
  return labels[value] || value
}

/** 图片资产副标题：imageType + businessSubtype（缺则 "待补充分类"） */
export function getAssetSubtitle(asset: WritingMaterialImageAsset): string {
  const parts = [
    formatAssetLabel(asset.imageType, IMAGE_TYPE_LABELS),
    formatAssetLabel(asset.businessSubtype, BUSINESS_SUBTYPE_LABELS),
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(' / ') : '待补充分类'
}
