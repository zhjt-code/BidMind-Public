/**
 * 写标素材分组工具防御测试。
 *
 * 这些纯函数从 WritingMaterials.tsx 抽出，承载素材展示的核心规则：
 * - 子素材分组优先级（ignored > table > image > mixed > text）
 * - 主复核徽章优先级（ignored > image > needs_review > null）
 * - 页码范围格式化
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatMaterialTypeLabel,
  formatPageRange,
  getAssetSubtitle,
  getChildGroupKey,
  getMaterialSortPage,
  getMaterialTypeOrder,
  getPrimaryReviewBadge,
} from '../src/pages/writing/WritingMaterials/materialGrouping.ts'
import { formatChecklistSubRequirement } from '../src/pages/writing/WritingMaterials/materialChecklistDisplay.ts'
import type { WritingMaterial, WritingMaterialImageAsset } from '../src/services/writingApi.ts'

function buildMaterial(overrides: Partial<WritingMaterial> = {}): WritingMaterial {
  return {
    id: 1,
    projectId: 1,
    title: 't',
    displayTitle: null,
    effectiveTitle: '',
    materialType: 'text',
    fileName: '',
    fileSize: 0,
    filePath: '',
    fileFormat: '',
    pageCount: null,
    sourcePage: null,
    sourcePageStart: null,
    sourcePageEnd: null,
    processingStatus: 'completed',
    sectionMatchCount: 0,
    chunkCount: 0,
    imageCount: 0,
    validatedImageCount: 0,
    reviewImageCount: 0,
    imageAssets: [],
    parentMaterialId: null,
    materialRole: 'normal',
    structureHint: null,
    structureHintLabel: null,
    splitConfidence: null,
    splitReviewStatus: null,
    kbSourceId: null,
    kbSourceVersion: null,
    kbAtomId: null,
    createdAt: null,
    ...overrides,
  } as WritingMaterial
}

test('getChildGroupKey: ignored 优先级最高', () => {
  assert.equal(
    getChildGroupKey(buildMaterial({ splitReviewStatus: 'ignored', materialType: 'table' })),
    'ignored',
  )
})

test('getChildGroupKey: table 类型归为 table 组', () => {
  assert.equal(getChildGroupKey(buildMaterial({ materialType: 'table' })), 'table')
})

test('getChildGroupKey: image 类型归为 image 组', () => {
  assert.equal(getChildGroupKey(buildMaterial({ materialType: 'image' })), 'image')
})

test('getChildGroupKey: text 类型 + imageCount>0 归为 mixed 组', () => {
  assert.equal(
    getChildGroupKey(buildMaterial({ materialType: 'text', imageCount: 2 })),
    'mixed',
  )
})

test('getChildGroupKey: 纯文本无图片归为 text 组', () => {
  assert.equal(getChildGroupKey(buildMaterial({ materialType: 'text' })), 'text')
})

test('getPrimaryReviewBadge: ignored 优先返回已忽略', () => {
  const badge = getPrimaryReviewBadge(
    buildMaterial({ splitReviewStatus: 'ignored', reviewImageCount: 3 }),
  )
  assert.equal(badge?.label, '已忽略')
})

test('getPrimaryReviewBadge: 非 ignored 时 reviewImageCount>0 优先于 needs_review', () => {
  const badge = getPrimaryReviewBadge(
    buildMaterial({ reviewImageCount: 5, splitReviewStatus: 'needs_review' }),
  )
  assert.equal(badge?.label, '图片待复核 5 张')
})

test('getPrimaryReviewBadge: 仅 needs_review 时显示待确认', () => {
  const badge = getPrimaryReviewBadge(buildMaterial({ splitReviewStatus: 'needs_review' }))
  assert.equal(badge?.label, '待确认')
})

test('getPrimaryReviewBadge: 已完成无异常返回 null（不显示徽章）', () => {
  assert.equal(getPrimaryReviewBadge(buildMaterial()), null)
})

test('formatPageRange: 有 start 时按 P{a}-P{b} 格式化', () => {
  assert.equal(formatPageRange(buildMaterial({ sourcePageStart: 3, sourcePageEnd: 7 })), 'P3-P7')
})

test('formatPageRange: 仅 sourcePage 时按 P{n} 格式化', () => {
  assert.equal(formatPageRange(buildMaterial({ sourcePage: 5 })), 'P5')
})

test('formatPageRange: 无页码返回 null', () => {
  assert.equal(formatPageRange(buildMaterial()), null)
})

test('formatMaterialTypeLabel: 已知类型映射为中文', () => {
  assert.equal(formatMaterialTypeLabel(buildMaterial({ materialType: 'text' })), '文本')
  assert.equal(formatMaterialTypeLabel(buildMaterial({ materialType: 'image' })), '图片')
  assert.equal(formatMaterialTypeLabel(buildMaterial({ materialType: 'certificate' })), '证照')
})

test('formatMaterialTypeLabel: 未知类型回退为中文兜底', () => {
  assert.equal(formatMaterialTypeLabel(buildMaterial({ materialType: 'unknown_x' })), '待识别素材')
})

test('getMaterialTypeOrder: text<table<image<其它', () => {
  assert.equal(getMaterialTypeOrder('text'), 0)
  assert.equal(getMaterialTypeOrder('table'), 1)
  assert.equal(getMaterialTypeOrder('image'), 2)
  assert.equal(getMaterialTypeOrder('certificate'), 3)
})

test('getMaterialSortPage: 优先 sourcePageStart 其次 sourcePage 最后 MAX_SAFE_INTEGER', () => {
  assert.equal(getMaterialSortPage(buildMaterial({ sourcePageStart: 5, sourcePage: 9 })), 5)
  assert.equal(getMaterialSortPage(buildMaterial({ sourcePage: 9 })), 9)
  assert.equal(getMaterialSortPage(buildMaterial()), Number.MAX_SAFE_INTEGER)
})

function buildAsset(overrides: Partial<WritingMaterialImageAsset> = {}): WritingMaterialImageAsset {
  return {
    id: 1,
    materialId: 1,
    imageType: null,
    businessSubtype: null,
    caption: null,
    reviewStatus: 'completed',
    ...overrides,
  } as WritingMaterialImageAsset
}

test('getAssetSubtitle: 含 imageType + businessSubtype 时拼接', () => {
  assert.equal(
    getAssetSubtitle(buildAsset({ imageType: '证书', businessSubtype: '资质' })),
    '证书 / 资质',
  )
})

test('getAssetSubtitle: 英文图片类型和业务子类型转为中文', () => {
  assert.equal(
    getAssetSubtitle(buildAsset({ imageType: 'certificate', businessSubtype: 'business_license' })),
    '证照 / 营业执照',
  )
})

test('getAssetSubtitle: 缺字段返回待补充分类', () => {
  assert.equal(getAssetSubtitle(buildAsset()), '待补充分类')
})

test('formatChecklistSubRequirement: 子项 facet 与技术参数均不裸出', () => {
  assert.equal(formatChecklistSubRequirement('comm_delivery'), '工期/交付')
  const cleaned = formatChecklistSubRequirement('缺少证书 relevance_score=10')
  assert.ok(!cleaned.toLowerCase().includes('relevance_score'))
  assert.ok(cleaned.includes('相关性评分10'))
})
