import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildCollusionInterpretationSections,
  getCollusionDecisionSummaryText,
} from '../src/pages/collusionResult/collusionInterpretationSections.ts'

test('串标概览说明应将限制、事实、AI研判和建议拆成稳定分组', () => {
  const sections = buildCollusionInterpretationSections({
    hasTenderDoc: false,
    overviewNotices: ['当前项目的对比覆盖率未达到 100%，整体结论需结合明细谨慎使用。'],
    riskConsistencyNote: '风险等级按群组口径展示。',
    decisionEngineVersion: 2,
    narrativeSummary: '未发现可支撑串标认定的异常证据。',
    factualExplanation: '已完成 1 对文件交叉比对。',
    synthesisSummary: 'AI 综合研判认为当前证据不足。',
    investigationSuggestions: ['优先查看高相似文件对。'],
  })

  assert.deepEqual(sections.map(section => section.title), [
    'AI 综合研判',
    '判断依据',
    '事实依据',
    '调查建议',
    '使用限制',
  ])

  const limitation = sections.find(section => section.title === '使用限制')
  assert.ok(limitation)
  assert.equal(limitation!.tone, 'warning')
  assert.ok(limitation!.items.length > 1, '使用限制含多条时应供 UI 渲染序号列表')
  assert.equal(limitation!.items.some(item => item.includes('未上传招标文件参考')), true)
  assert.equal(limitation!.items.some(item => item.includes('覆盖率未达到 100%')), true)
  assert.equal(limitation!.items.some(item => item.includes('群组口径')), true)

  const fact = sections.find(section => section.title === '事实依据')
  assert.ok(fact)
  assert.equal(fact!.badge, undefined)

  const synthesis = sections.find(section => section.title === 'AI 综合研判')
  assert.ok(synthesis)
  assert.equal(synthesis!.badge, undefined)
})

test('无内容的说明分组不应渲染空节', () => {
  const sections = buildCollusionInterpretationSections({
    hasTenderDoc: true,
    overviewNotices: [],
    riskConsistencyNote: undefined,
    decisionEngineVersion: 2,
  })

  assert.deepEqual(sections, [])
})

test('事实依据应优先使用结构化条目且使用限制去除同义重复提示', () => {
  const sections = buildCollusionInterpretationSections({
    hasTenderDoc: false,
    overviewNotices: [
      '未上传招标文件参照，部分文本一致性可能来源于招标模板，请人工甄别。',
      '本次分析未额外依赖稳定行业先验，系统已按保守口径判断文本相似与风险，请结合明细证据综合判断。',
    ],
    riskConsistencyNote: undefined,
    decisionEngineVersion: 2,
    factualExplanationItems: [
      '本次共完成 1 对文件交叉比对（覆盖率 100%），其中 1 对已转人工复核或分析失败。',
      'BJ投标1.pdf × CK投标2.pdf：白名单命中（通用格式模板），重合可能源于通用模板；当前有效证据不足以判定串标风险，已转人工复核',
      '行业画像：AI 已读取材料但未能归纳出明确行业类型，行业先验缺失，D3/D4/D5 已退化为不依赖行业先验的保守判定路径。',
    ],
  })

  const fact = sections.find(section => section.title === '事实依据')
  assert.ok(fact)
  assert.deepEqual(fact!.items, [
    '本次共完成 1 对文件交叉比对（覆盖率 100%），其中 1 对已转人工复核或分析失败。',
    'BJ投标1.pdf × CK投标2.pdf：白名单命中（通用格式模板），重合可能源于通用模板；当前有效证据不足以判定串标风险，已转人工复核',
    '行业画像：AI 已读取材料但未能归纳出明确行业类型，行业先验缺失，D3/D4/D5 已退化为不依赖行业先验的保守判定路径。',
  ])

  const limitation = sections.find(section => section.title === '使用限制')
  assert.ok(limitation)
  assert.equal(
    limitation!.items.filter(item => item.includes('未上传招标文件')).length,
    1,
  )
})

test('项目最终结论说明应按结论状态输出具体业务文案', () => {
  assert.equal(
    getCollusionDecisionSummaryText({
      projectDecision: 'suspicious_found',
      reviewRequired: false,
    }),
    '系统已发现可支撑进一步核查的异常线索，请结合下方证据明细确认。'
  )
  assert.equal(
    getCollusionDecisionSummaryText({
      projectDecision: 'clear_no_risk',
      reviewRequired: false,
    }),
    '系统未发现可支撑串标认定的异常证据。'
  )
  assert.equal(
    getCollusionDecisionSummaryText({
      projectDecision: 'partial_review_needed',
      reviewRequired: true,
    }),
    '当前仍有文件对需要人工复核，请结合下方证据明细判断。'
  )
})
