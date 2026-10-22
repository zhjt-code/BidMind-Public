import assert from 'node:assert/strict'
import test from 'node:test'

import { extractionWarningBannerTitle } from '../src/pages/RequirementReview/extractionWarningCopy.ts'
import {
  formatExtractionWarning,
  normalizeRequirementPages,
  transformScoringTotalsReport,
  transformRequirement,
} from '../src/pages/requirementReviewTransforms.ts'

test('normalizeRequirementPages 优先消费 source_pages 并去重升序', () => {
  assert.deepEqual(
    normalizeRequirementPages([7, 3, 7, 12], 99),
    [3, 7, 12],
    'source_pages 命中后应忽略 source_page 兜底值',
  )
})

test('normalizeRequirementPages 数组为空时回退到 source_page', () => {
  assert.deepEqual(normalizeRequirementPages([], 5), [5])
  assert.deepEqual(normalizeRequirementPages(undefined, 8), [8])
})

test('normalizeRequirementPages 过滤非法页码（0/负数/字符串/NaN）', () => {
  assert.deepEqual(normalizeRequirementPages([0, -1, 'abc', 4], null), [4])
  assert.deepEqual(normalizeRequirementPages([], 0), [])
  assert.deepEqual(normalizeRequirementPages([], -3), [])
})

test('transformRequirement 应映射 documentId / source_pages / mergedCount 等溯源字段', () => {
  const req = transformRequirement({
    id: 12,
    project_id: 4,
    document_id: 87,
    category: 'qualification',
    description: '投标人需提供营业执照及资质证书',
    is_mandatory: true,
    confirmed: false,
    source_page: 3,
    source_pages: [3, 5, 5],
    keywords: ['营业执照'],
    sub_requirements: ['营业执照', '资质证书', ''],
    merged_count: 2,
  })

  assert.equal(req.id, 12)
  assert.equal(req.documentId, 87)
  assert.equal(req.sourcePage, 3)
  assert.deepEqual(req.sourcePages, [3, 5])
  assert.equal(req.mergedCount, 2)
  assert.deepEqual(req.subRequirements, ['营业执照', '资质证书'])
})

test('transformRequirement 在 source_pages 缺失时退化到 source_page 单页', () => {
  const req = transformRequirement({
    id: 5,
    project_id: 1,
    document_id: 2,
    category: 'commercial',
    description: '投标保证金 5 万元，递交方式为银行转账',
    is_mandatory: false,
    confirmed: false,
    source_page: 9,
  })

  assert.deepEqual(req.sourcePages, [9])
  assert.equal(req.sourcePage, 9)
  assert.equal(req.mergedCount, null)
  assert.deepEqual(req.subRequirements, [])
})

test('transformRequirement 对手动添加的无来源条目不伪造页码', () => {
  const req = transformRequirement({
    id: 11,
    project_id: 1,
    document_id: null,
    category: 'technical',
    description: '人工补充：所有材料必须加盖公章',
    is_mandatory: true,
    confirmed: false,
    source_page: null,
    source_pages: [],
  })

  assert.equal(req.documentId, null)
  assert.equal(req.sourcePage, null)
  assert.deepEqual(req.sourcePages, [])
})

test('formatExtractionWarning 将旧字段校验提示改写为用户可理解文案', () => {
  const warning = formatExtractionWarning('另有 1 条要求因字段校验失败被丢弃（已自动抢救有效条目）')

  assert.equal(
    warning.summary,
    '有 1 条疑似要求未能自动整理成列表。系统已保留其他有效要求，请结合原文重点核对。',
  )
  assert.deepEqual(warning.details, [])
})

test('formatExtractionWarning 将旧废标信号提示改写成招标要求核对说明', () => {
  const warning = formatExtractionWarning(
    '有 20 处废标/无效投标信号未能对应到独立审查条件（涉及第 20、23、25、26、40、41 页），请对照原文核对否决项是否漏提或被合并',
  )

  assert.equal(warning.kind, 'mandatory')
  assert.equal(warning.tone, 'action')
  assert.equal(
    warning.summary,
    '第 20、23、25、26、40、41 页有 20 处写明「废标」或「投标无效」的内容，还没有提取成单独的一条招标要求。请对照原文检查：是漏了，还是几条被合成了一条。',
  )
})

test('formatExtractionWarning 将旧补标提示改写成已自动处理说明', () => {
  const warning = formatExtractionWarning(
    '已自动核验高风险条款：补标否决 13 条，补提独立条件 13 条',
  )

  assert.equal(warning.kind, 'mandatory')
  assert.equal(warning.tone, 'info')
  assert.equal(
    warning.summary,
    '系统已自动处理：13 条已有要求已标为否决项，另新增 13 条独立否决条件。',
  )
})

test('formatExtractionWarning 将旧枚举缺口提示改写成招标要求核对说明', () => {
  const warning = formatExtractionWarning(
    '高风险章节内可逐条枚举的条件比已提取条目多 4 条，请核对数序号或★标记条款是否全部进入确认列表',
  )

  assert.equal(warning.kind, 'mandatory')
  assert.equal(warning.tone, 'action')
  assert.equal(
    warning.summary,
    '否决或资格相关章节里，带序号或★的条件比当前招标要求多 4 条。请核对这些条款是否都已提取出来。',
  )
})

test('extractionWarningBannerTitle 否决项待核对应使用核对标题', () => {
  const displays = [
    formatExtractionWarning(
      '第 20 页有 2 处写明「废标」或「投标无效」的内容，还没有提取成单独的一条招标要求。请对照原文检查：是漏了，还是几条被合成了一条。',
    ),
    formatExtractionWarning('系统已自动处理：13 条已有要求已标为否决项，另新增 13 条独立否决条件。'),
  ]
  assert.equal(extractionWarningBannerTitle(displays), '请先核对否决项是否齐全')
})

test('extractionWarningBannerTitle 仅自动处理时不改成否决项核对标题', () => {
  const displays = [
    formatExtractionWarning('系统已自动处理：3 条已有要求已标为否决项。'),
  ]
  assert.equal(extractionWarningBannerTitle(displays), '部分否决项已自动处理')
})

test('extractionWarningBannerTitle 普通提取警告仍用通用标题', () => {
  const displays = [
    formatExtractionWarning('另有 1 条要求因字段校验失败被丢弃（已自动抢救有效条目）'),
  ]
  assert.equal(extractionWarningBannerTitle(displays), '部分内容需要人工核对')
})

test('formatExtractionWarning 保留后端返回的具体核对明细', () => {
  const warning = formatExtractionWarning(
    '有 2 条疑似要求未能自动整理成列表。系统已保留其他有效要求，请人工核对以下位置:\n'
    + '- 第 5 页：投标人须提供营业执照（要求分类无法识别）\n'
    + '- 第 8 页：第 2 个自动提取条目（要求内容为空或过短）',
  )

  assert.equal(
    warning.summary,
    '有 2 条疑似要求未能自动整理成列表。系统已保留其他有效要求，请人工核对以下位置',
  )
  assert.deepEqual(warning.details, [
    '第 5 页：投标人须提供营业执照（要求分类无法识别）',
    '第 8 页：第 2 个自动提取条目（要求内容为空或过短）',
  ])
})

test('transformScoringTotalsReport 保留按卷合计供前端显示权威核计结果', () => {
  const report = transformScoringTotalsReport({
    needs_review: false,
    note: '评分项满分校验通过',
    by_volume: { technical: 100 },
    volume_checks: {
      technical: { sum: 100, expected: 100, weight: 0.4, within: true },
    },
    weight_sum_ok: false,
    item_count: 16,
    missing_score_count: 0,
  })

  assert.notEqual(report, null)
  assert.equal(report?.needsReview, false)
  assert.deepEqual(report?.byVolume, { technical: 100 })
  assert.equal(report?.volumeChecks.technical.within, true)
})
