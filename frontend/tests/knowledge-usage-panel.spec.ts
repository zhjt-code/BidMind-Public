import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  KNOWLEDGE_USAGE_HINT,
  KNOWLEDGE_USAGE_LABEL,
  KNOWLEDGE_USAGE_MODE_OPTIONS,
  canSaveKnowledgeUsage,
  enabledFromKnowledgeUsageMode,
  formatKnowledgeUsageReadOnlySummary,
  isKnowledgeUsageDirty,
  knowledgeUsageModeFromEnabled,
  shouldRenderReadOnlyKnowledgeUsagePanel,
} from '../src/components/knowledgeUsagePanelView.ts'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

test('只读知识库摘要在未实际启用时不渲染', () => {
  assert.equal(
    shouldRenderReadOnlyKnowledgeUsagePanel({
      loading: false,
      enabled: false,
      configFlow: 'bid_review',
      currentFlow: 'bid_review',
      selectedDocumentIds: [],
    }),
    false,
  )

  assert.equal(
    shouldRenderReadOnlyKnowledgeUsagePanel({
      loading: false,
      enabled: true,
      configFlow: 'collusion_check',
      currentFlow: 'bid_review',
      selectedDocumentIds: [1],
    }),
    false,
  )

  assert.equal(
    shouldRenderReadOnlyKnowledgeUsagePanel({
      loading: false,
      enabled: true,
      configFlow: 'bid_review',
      currentFlow: 'bid_review',
      selectedDocumentIds: [],
    }),
    false,
  )
})

test('只读知识库摘要加载中或实际启用时保留展示', () => {
  assert.equal(
    shouldRenderReadOnlyKnowledgeUsagePanel({
      loading: true,
      enabled: false,
      configFlow: null,
      currentFlow: 'collusion_check',
      selectedDocumentIds: [],
    }),
    true,
  )

  assert.equal(
    shouldRenderReadOnlyKnowledgeUsagePanel({
      loading: false,
      enabled: true,
      configFlow: 'collusion_check',
      currentFlow: 'collusion_check',
      selectedDocumentIds: [3, 5],
    }),
    true,
  )
})

test('知识库仪器文案为关闭与参考且按流程区分问号', () => {
  assert.equal(KNOWLEDGE_USAGE_LABEL, '知识库')
  assert.deepEqual(
    KNOWLEDGE_USAGE_MODE_OPTIONS.map((option) => option.label),
    ['关闭', '参考'],
  )
  assert.equal(knowledgeUsageModeFromEnabled(false), 'off')
  assert.equal(knowledgeUsageModeFromEnabled(true), 'on')
  assert.equal(enabledFromKnowledgeUsageMode('off'), false)
  assert.equal(enabledFromKnowledgeUsageMode('on'), true)
  assert.match(KNOWLEDGE_USAGE_HINT.bid_review, /招标原文和投标响应/)
  assert.match(KNOWLEDGE_USAGE_HINT.collusion_check, /不会单独定串标/)
  assert.equal(formatKnowledgeUsageReadOnlySummary(2), '知识库 · 已参考 2 份')
})

test('未保存改动才可保存，关闭已启用配置仍可保存', () => {
  assert.equal(
    isKnowledgeUsageDirty({
      configEnabled: false,
      configFlow: 'bid_review',
      currentFlow: 'bid_review',
      enabled: false,
      selectedIds: [],
      savedIds: [],
    }),
    false,
  )
  assert.equal(
    canSaveKnowledgeUsage(false, false, []),
    false,
  )
  assert.equal(
    isKnowledgeUsageDirty({
      configEnabled: true,
      configFlow: 'collusion_check',
      currentFlow: 'collusion_check',
      enabled: false,
      selectedIds: [1],
      savedIds: [1],
    }),
    true,
  )
  assert.equal(canSaveKnowledgeUsage(true, false, []), true)
  assert.equal(canSaveKnowledgeUsage(true, true, []), false)
  assert.equal(canSaveKnowledgeUsage(true, true, [8]), true)
})

test('串标上传与招标确认把知识库仪器挂在标题行而不是内容区第一卡', () => {
  const collusion = readSrc('pages/CollusionUpload.tsx')
  const review = readSrc('pages/RequirementReview/index.tsx')
  const header = readSrc('pages/RequirementReview/components/RequirementHeader.tsx')
  const panel = readSrc('components/KnowledgeUsagePanel.tsx')

  assert.match(collusion, /flex flex-wrap items-center justify-end gap-3/)
  assert.match(collusion, /flow="collusion_check"/)
  assert.doesNotMatch(collusion, /space-y-6[\s\S]{0,120}<KnowledgeUsagePanel/)
  assert.doesNotMatch(collusion, /mb-3 flex justify-end/)
  assert.match(review, /knowledgeSlot=/)
  assert.doesNotMatch(review, /className="mb-4"[\s\S]{0,80}<KnowledgeUsagePanel/)
  assert.match(header, /knowledgeSlot/)
  assert.match(panel, /SegmentedControl/)
  assert.doesNotMatch(panel, /保存授权/)
  assert.doesNotMatch(panel, /启用知识库/)
})
