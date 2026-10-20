import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  CATEGORY_GROUP_MAP,
  QA_CATEGORY_ORDER,
  QA_GROUP_ORDER,
  QA_IMAGE_PAGE_HINT,
  countFindings,
  filterFindings,
  findingBidParts,
  groupOfCategory,
  imagePageNotices,
  pendingFindingCount,
  sortFindings,
  transformDocumentQuality,
} from '../src/pages/result/documentQuality/documentQualityTransforms.ts'
import {
  DEFAULT_QA_FILTERS,
  type DocumentQualityData,
  type QaFinding,
} from '../src/pages/result/documentQuality/types.ts'

const ALL_CATEGORIES = [
  'structure', 'toc', 'layout', 'language', 'duplication',
  'numbering', 'consistency', 'reference', 'hygiene', 'integrity',
]

function finding(partial: Partial<QaFinding> & { id: number }): QaFinding {
  return {
    ruleCode: 'DOC-X',
    category: 'toc',
    severity: 'P2',
    certainty: 'probable',
    title: 't',
    message: 'm',
    suggestion: null,
    evidence: { kind: 'single', items: [] },
    documentId: null,
    disposition: 'pending',
    dispositionNote: null,
    version: 0,
    fingerprint: `fp${partial.id}`,
    ...partial,
  }
}

test('十类后端分类全部映射到五个前端分组', () => {
  for (const category of ALL_CATEGORIES) {
    const group = groupOfCategory(category)
    assert.ok(QA_GROUP_ORDER.includes(group), `${category} 未映射到已知分组`)
  }
  assert.equal(Object.keys(CATEGORY_GROUP_MAP).length, ALL_CATEGORIES.length)
  assert.deepEqual([...QA_CATEGORY_ORDER].slice().sort(), [...ALL_CATEGORIES].slice().sort())
  // 关键归属抽查(结构导航与内容准确性两组)
  assert.equal(groupOfCategory('numbering'), 'structure_nav')
  assert.equal(groupOfCategory('consistency'), 'content_accuracy')
})

test('默认排序: 待处理优先, 其次严重度, 再按首证据页', () => {
  const sorted = sortFindings([
    finding({ id: 1, severity: 'P3', disposition: 'pending' }),
    finding({ id: 2, severity: 'P0', disposition: 'ignored' }),
    finding({
      id: 3, severity: 'P0', disposition: 'pending',
      evidence: { kind: 'single', items: [{ documentId: 1, bidPart: null, page: 9, excerpt: '', bbox: null, role: 'primary' }] },
    }),
    finding({
      id: 4, severity: 'P0', disposition: 'pending',
      evidence: { kind: 'single', items: [{ documentId: 1, bidPart: null, page: 2, excerpt: '', bbox: null, role: 'primary' }] },
    }),
  ])
  assert.deepEqual(sorted.map(f => f.id), [4, 3, 1, 2])
})

function pageEvidence(page: number): QaFinding['evidence'] {
  return {
    kind: 'single',
    items: [{ documentId: 1, bidPart: null, page, excerpt: '', bbox: null, role: 'primary' }],
  }
}

test('同严重度内按类型聚簇, 页码只做同类内次序(编号夹具)', () => {
  const sorted = sortFindings([
    finding({ id: 10, severity: 'P1', category: 'consistency', evidence: pageEvidence(2) }),
    finding({ id: 11, severity: 'P1', category: 'numbering', evidence: pageEvidence(10) }),
    finding({ id: 12, severity: 'P1', category: 'numbering', evidence: pageEvidence(3) }),
  ])
  assert.deepEqual(sorted.map(f => f.id), [12, 11, 10])
})

test('同严重度内按类型聚簇, 页码只做同类内次序(目录/卫生第二实体)', () => {
  const sorted = sortFindings([
    finding({ id: 20, severity: 'P2', category: 'hygiene', evidence: pageEvidence(1) }),
    finding({ id: 21, severity: 'P2', category: 'toc', evidence: pageEvidence(20) }),
    finding({ id: 22, severity: 'P2', category: 'toc', evidence: pageEvidence(5) }),
  ])
  assert.deepEqual(sorted.map(f => f.id), [22, 21, 20])
})

test('筛选与联动计数', () => {
  const findings = [
    finding({ id: 1, category: 'toc', severity: 'P1' }),
    finding({ id: 2, category: 'hygiene', severity: 'P2', disposition: 'confirmed' }),
    finding({ id: 3, category: 'consistency', severity: 'P0' }),
  ]
  const filtered = filterFindings(findings, { ...DEFAULT_QA_FILTERS, group: 'structure_nav' })
  assert.deepEqual(filtered.map(f => f.id), [1])

  const counts = countFindings(findings, { ...DEFAULT_QA_FILTERS, severity: 'P0' })
  // 分组计数在"严重度=P0"生效下统计: 只剩 consistency -> content_accuracy
  assert.equal(counts.byGroup.content_accuracy, 1)
  assert.equal(counts.byGroup.all, 1)
  // 严重度轴计数不受自身筛选影响
  assert.equal(counts.bySeverity.P1, 1)
  assert.equal(counts.bySeverity.all, 3)
})

test('DTO 转换与待处理计数', () => {
  const data: DocumentQualityData = transformDocumentQuality({
    scan: {
      status: 'completed',
      ruleset_version: 'v1',
      generated_at: '2026-09-15T00:00:00',
      documents: [
        { document_id: 11, bid_part: 'commercial', filename: 'a.pdf', page_count: 10, status: 'completed' },
      ],
      checkers: [{ code: 'toc', status: 'ok' }],
      error_message: null,
    },
    summary: {
      total: 1,
      by_category: { toc: 1 },
      by_severity: { P1: 1 },
      by_certainty: { probable: 1 },
      by_disposition: { pending: 1 },
    },
    findings: [{
      id: 5,
      rule_code: 'DOC-TOC-001',
      category: 'toc',
      severity: 'P1',
      certainty: 'probable',
      title: '目录条目未找到对应正文',
      message: 'x',
      suggestion: null,
      evidence: { kind: 'single', items: [{ document_id: 11, bid_part: 'commercial', page: 3, excerpt: 'y', bbox: [0.1, 0.2, 0.5, 0.25], role: 'primary' }] },
      document_id: 11,
      disposition: 'pending',
      disposition_note: null,
      version: 0,
      fingerprint: 'abc',
    }],
  })
  assert.equal(data.findings[0].ruleCode, 'DOC-TOC-001')
  assert.deepEqual(data.findings[0].evidence.items[0].bbox, [0.1, 0.2, 0.5, 0.25])
  assert.equal(data.scan.documents[0].bidPart, 'commercial')
  // 旧结果没有图片页记录
  assert.equal(data.scan.documents[0].imagePageCount, null)
  assert.equal(pendingFindingCount(data), 1)
  assert.equal(pendingFindingCount(null), 0)
})

function scanDocument(
  documentId: number,
  bidPart: string | null,
  imagePageCount: number | null,
): DocumentQualityData['scan']['documents'][number] {
  return {
    documentId, bidPart, filename: `${documentId}.pdf`, pageCount: 74, status: 'completed', imagePageCount,
  }
}

test('扫描件/图片页数从 DTO 映射, 整本只说页数', () => {
  const data = transformDocumentQuality({
    scan: {
      status: 'completed',
      documents: [
        { document_id: 11, bid_part: null, filename: 'a.pdf', page_count: 74, status: 'completed', image_page_count: 44 },
      ],
      checkers: [],
    },
    summary: {},
    findings: [],
  })
  assert.equal(data.scan.documents[0].imagePageCount, 44)
  const notices = imagePageNotices(data.scan.documents, part => `卷${part}`)
  assert.deepEqual(notices, [
    { documentId: 11, text: '其中 44 页为扫描件或图片，不在质检范围内' },
  ])
})

test('分卷时说明带卷名, 没有图片页或旧结果的卷不出说明(商务/技术第二实体)', () => {
  const labels: Record<string, string> = { commercial: '商务标', technical: '技术标' }
  const notices = imagePageNotices(
    [
      scanDocument(21, 'commercial', 3),
      scanDocument(22, 'technical', 12),
      scanDocument(23, 'technical', 0),
      scanDocument(24, 'commercial', null),
    ],
    part => labels[part] ?? part,
  )
  assert.deepEqual(notices.map(n => n.text), [
    '商务标：其中 3 页为扫描件或图片，不在质检范围内',
    '技术标：其中 12 页为扫描件或图片，不在质检范围内',
  ])
})

test('整本证据 bid_part 为空时不产出分卷徽标', () => {
  const integral = finding({
    id: 30,
    evidence: {
      kind: 'single',
      items: [{ documentId: 11, bidPart: null, page: 3, excerpt: 'x', bbox: null, role: 'primary' }],
    },
  })
  assert.deepEqual(findingBidParts(integral), [])
})

test('分卷证据按 bid_part 去重产出卷徽标(商务/技术第二实体)', () => {
  const commercial = finding({
    id: 31,
    evidence: {
      kind: 'single',
      items: [
        { documentId: 21, bidPart: 'commercial', page: 2, excerpt: 'a', bbox: null, role: 'primary' },
        { documentId: 21, bidPart: 'commercial', page: 8, excerpt: 'b', bbox: null, role: 'comparison' },
      ],
    },
  })
  const technical = finding({
    id: 32,
    evidence: {
      kind: 'pair',
      items: [
        { documentId: 21, bidPart: 'commercial', page: 4, excerpt: 'c', bbox: null, role: 'primary' },
        { documentId: 22, bidPart: 'technical', page: 9, excerpt: 'd', bbox: null, role: 'comparison' },
      ],
    },
  })
  assert.deepEqual(findingBidParts(commercial), ['commercial'])
  assert.deepEqual(findingBidParts(technical), ['commercial', 'technical'])
})

const qaDir = join(dirname(fileURLToPath(import.meta.url)), '../src/pages/result/documentQuality')

function readQa(rel: string): string {
  return readFileSync(join(qaDir, rel), 'utf8')
}

test('质检展示用 bid_part 分卷, 不把规则编码当文档标识, 不展示检查器覆盖率', () => {
  const stats = readQa('DocumentQualityStats.tsx')
  const detail = readQa('DocumentQualityDetail.tsx')
  const list = readQa('DocumentQualityList.tsx')
  assert.doesNotMatch(stats, /检查器 \{/)
  assert.doesNotMatch(stats, /checkersOk/)
  assert.match(stats, /bidPartLabel/)
  assert.match(detail, /findingBidParts/)
  assert.doesNotMatch(detail, /finding\.ruleCode/)
  assert.match(list, /findingBidParts/)
  assert.match(list, /bidPartLabel\(part\)/)
})

test('图片页说明用灰色中性提示并带原因, 不进黄色告警条', () => {
  const stats = readQa('DocumentQualityStats.tsx')
  assert.match(stats, /imagePageNotices\(scan\.documents, bidPartLabel\)/)
  assert.match(stats, /title=\{QA_IMAGE_PAGE_HINT\}/)
  assert.match(stats, /text-xs text-gray-500/)
  const noticeBlock = stats.slice(stats.indexOf('scopeNotices.map'), stats.indexOf('scanAbnormal ||'))
  assert.doesNotMatch(noticeBlock, /amber/)
  assert.match(QA_IMAGE_PAGE_HINT, /视为已定稿的正式材料/)
  assert.match(QA_IMAGE_PAGE_HINT, /文字被转成图形的页面也按图片处理/)
})
