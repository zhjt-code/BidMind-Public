import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  getClauseJumpPage,
  getClausePageLabel,
  getClausePageText,
  getTraceabilityStatus,
  isMultiPageClause,
  transformWritingClause,
} from '../src/services/writingClauseTransforms.ts'

const writingClausesSource = readFileSync(
  new URL('../src/pages/writing/WritingClauses.tsx', import.meta.url),
  'utf8',
)

test('transformWritingClause 应映射人工新增标识与排序字段', () => {
  const clause = transformWritingClause({
    id: 18,
    project_id: 7,
    document_id: 31,
    layer: 'structure_constraint',
    sub_type: 'content_required',
    content: '手工补充条目',
    raw_text: null,
    source_page: 12,
    score_weight: null,
    confirmed: 0,
    is_manual: 1,
    sort_order: 4,
  })

  assert.equal(clause.id, 18)
  assert.equal(clause.documentId, 31)
  assert.equal(clause.isManual, 1)
  assert.equal(clause.sortOrder, 4)
})

test('transformWritingClause 在缺失 is_manual 时默认回退为 0', () => {
  const clause = transformWritingClause({
    id: 19,
    project_id: 7,
    document_id: 31,
    layer: 'generation_guide',
    sub_type: 'project_context',
    content: '项目背景',
    raw_text: '原文',
    source_page: null,
    score_weight: null,
    confirmed: 1,
    sort_order: 5,
  })

  assert.equal(clause.isManual, 0)
  assert.equal(clause.confirmed, 1)
  assert.equal(clause.rawText, '原文')
})

test('transformWritingClause 应将空白原文摘录归一化为 null', () => {
  const clause = transformWritingClause({
    id: 20,
    project_id: 7,
    document_id: 31,
    layer: 'hard_constraint',
    sub_type: 'disqualification',
    content: '示例',
    raw_text: '   ',
    source_page: 4,
    score_weight: null,
    confirmed: 0,
    sort_order: 1,
  })

  assert.equal(clause.rawText, null)
  assert.equal(clause.sourcePage, 4)
})

test('getTraceabilityStatus 区分 has_excerpt / locatable_only / no_evidence 三态', () => {
  const base = {
    id: 1,
    projectId: 1,
    documentId: 1,
    layer: 'hard_constraint',
    subType: 'disqualification',
    content: 'x',
    scoreWeight: null,
    confirmed: 0,
    isManual: 0,
    sortOrder: 0,
    sourcePageStart: null,
    sourcePageEnd: null,
  }

  assert.equal(
    getTraceabilityStatus({ ...base, rawText: '原文摘录', sourcePage: 3 }),
    'has_excerpt',
  )
  assert.equal(
    getTraceabilityStatus({ ...base, rawText: null, sourcePage: 3 }),
    'locatable_only',
  )
  assert.equal(
    getTraceabilityStatus({ ...base, rawText: null, sourcePage: null }),
    'no_evidence',
  )
  assert.equal(
    getTraceabilityStatus({ ...base, rawText: '   ', sourcePage: 5 }),
    'locatable_only',
    '空白摘录应视为无摘录',
  )
  // 仅有页范围也应视为 locatable_only
  assert.equal(
    getTraceabilityStatus({
      ...base, rawText: null, sourcePage: null,
      sourcePageStart: 9, sourcePageEnd: 10,
    }),
    'locatable_only',
  )
})

test('transformWritingClause 应读取 source_page_start/end 并在缺失时做单页兜底', () => {
  const withRange = transformWritingClause({
    id: 21,
    project_id: 7,
    document_id: 31,
    layer: 'hard_constraint',
    sub_type: 'disqualification',
    content: '跨页约束',
    raw_text: null,
    source_page: 9,
    source_page_start: 9,
    source_page_end: 10,
    score_weight: null,
    confirmed: 0,
    sort_order: 0,
  })
  assert.equal(withRange.sourcePage, 9)
  assert.equal(withRange.sourcePageStart, 9)
  assert.equal(withRange.sourcePageEnd, 10)

  const legacySinglePage = transformWritingClause({
    id: 22,
    project_id: 7,
    document_id: 31,
    layer: 'hard_constraint',
    sub_type: 'disqualification',
    content: '老数据只有 source_page',
    raw_text: null,
    source_page: 12,
    score_weight: null,
    confirmed: 0,
    sort_order: 0,
  })
  // 老数据没有范围字段时, 前端应回退为单页范围, 避免展示 "P?-P?"
  assert.equal(legacySinglePage.sourcePageStart, 12)
  assert.equal(legacySinglePage.sourcePageEnd, 12)
})

test('getClausePageLabel / getClausePageText 按单页或跨页正确格式化', () => {
  const single = {
    id: 1, projectId: 1, documentId: 1,
    layer: 'hard_constraint', subType: 'disqualification',
    content: 'x', rawText: null, scoreWeight: null,
    confirmed: 0, isManual: 0, sortOrder: 0,
    sourcePage: 9, sourcePageStart: 9, sourcePageEnd: 9,
  }
  assert.equal(getClausePageLabel(single), 'P9')
  assert.equal(getClausePageText(single), '第 9 页')
  assert.equal(isMultiPageClause(single), false)

  const multi = { ...single, sourcePage: 9, sourcePageStart: 9, sourcePageEnd: 10 }
  assert.equal(getClausePageLabel(multi), 'P9-P10')
  assert.equal(getClausePageText(multi), '第 9-10 页')
  assert.equal(isMultiPageClause(multi), true)

  const empty = {
    ...single, sourcePage: null, sourcePageStart: null, sourcePageEnd: null,
  }
  assert.equal(getClausePageLabel(empty), '')
  assert.equal(getClausePageText(empty), '')
  assert.equal(isMultiPageClause(empty), false)

  // 只有 start 没有 end 的异常兜底: 视为单页
  const onlyStart = { ...single, sourcePage: null, sourcePageStart: 7, sourcePageEnd: null }
  assert.equal(getClausePageLabel(onlyStart), 'P7')
})

test('getClauseJumpPage 优先返回 sourcePage, 回退 start', () => {
  const base = {
    id: 1, projectId: 1, documentId: 1,
    layer: 'hard_constraint', subType: 'disqualification',
    content: 'x', rawText: null, scoreWeight: null,
    confirmed: 0, isManual: 0, sortOrder: 0,
  }
  assert.equal(
    getClauseJumpPage({ ...base, sourcePage: 9, sourcePageStart: 9, sourcePageEnd: 10 }),
    9,
  )
  // sourcePage 缺失时应回退到 start
  assert.equal(
    getClauseJumpPage({ ...base, sourcePage: null, sourcePageStart: 9, sourcePageEnd: 10 }),
    9,
  )
  // 全缺失时返回 null, 不得猜测
  assert.equal(
    getClauseJumpPage({ ...base, sourcePage: null, sourcePageStart: null, sourcePageEnd: null }),
    null,
  )
})

test('写标约束卡片保留审核主操作，新增和删除折叠到更多菜单', () => {
  assert.match(writingClausesSource, /DropdownMenu/)
  assert.match(writingClausesSource, /更多操作/)
  assert.match(writingClausesSource, /<Button[^>]*onClick=\{\(\) => startEdit\(clause\)\}/)
  assert.match(writingClausesSource, /<Button[^>]*onClick=\{\(\) => handleConfirmOne\(clause\.id\)\}/)
  assert.match(writingClausesSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => openAddDialog\(clause\.layer, clause\.subType, clause\.id\)\}/)
  assert.match(writingClausesSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => setDeleteTarget\(clause\)\}/)
  assert.doesNotMatch(writingClausesSource, /<Button[^>]*onClick=\{\(\) => openAddDialog\(clause\.layer, clause\.subType, clause\.id\)\}/)
  assert.doesNotMatch(writingClausesSource, /<Button[^>]*onClick=\{\(\) => setDeleteTarget\(clause\)\}/)
})

test('写标约束确认页应参照目录页使用主页面滚动而非小型内嵌滚动窗', () => {
  assert.match(writingClausesSource, /return \(\s*<div>\s*<WritingStepBar/)
  assert.match(writingClausesSource, /<div className="max-w-4xl mx-auto">/)
  assert.match(
    writingClausesSource,
    /<Tabs value=\{activeTab\} onValueChange=\{setActiveTab\}>/,
  )
  assert.match(
    writingClausesSource,
    /<TabsContent key=\{tab\.value\} value=\{tab\.value\} className="mt-4 pb-10">/,
  )
  assert.doesNotMatch(writingClausesSource, /<div className="flex h-full min-h-0 flex-col overflow-hidden">/)
  assert.doesNotMatch(writingClausesSource, /<Tabs[^>]*className="flex min-h-0 flex-1 flex-col"/)
  assert.doesNotMatch(writingClausesSource, /TabsContent[\s\S]*overflow-y-auto/)
})

test('写标约束确认页底部应保留温和呼吸区避免最后卡片贴边', () => {
  assert.match(
    writingClausesSource,
    /<TabsContent key=\{tab\.value\} value=\{tab\.value\} className="mt-4 pb-10">/,
  )
})
