import assert from 'node:assert/strict'
import test from 'node:test'
import {
  gateFlagsToBadges,
  hasAnyGateWarning,
  summarizeGateWarnings,
} from '../src/pages/writing/writingComposeGateFlags.ts'

test('gateFlagsToBadges: null/undefined 返回空', () => {
  assert.deepEqual(gateFlagsToBadges(null), [])
  assert.deepEqual(gateFlagsToBadges(undefined), [])
  assert.deepEqual(gateFlagsToBadges({}), [])
})

test('MaterialSourceTraceGate: material_trace_missing → 素材未引用 (normal)', () => {
  const flags = {
    MaterialSourceTraceGate: {
      verdict: 'reject',
      severity: 'advisory',
      reason: 'section did not reference any material_id',
      patch: { material_trace_missing: true },
      trace: { outcome: 'no_used_id' },
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges.length, 1)
  assert.equal(badges[0].key, 'material_trace_missing')
  assert.equal(badges[0].label, '素材未引用')
  assert.equal(badges[0].severity, 'normal')
})

test('MaterialSourceTraceGate: unbound_cited → 引用未绑定素材', () => {
  const flags = {
    MaterialSourceTraceGate: {
      verdict: 'reject',
      severity: 'advisory',
      reason: '',
      patch: { unbound_material_ids: [101, 202] },
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].key, 'unbound_cited')
  assert.ok(badges[0].hint.includes('101'))
  assert.equal(badges[0].severity, 'normal')
})

test('MaterialSourceTraceGate: low_confidence → 素材未绑定', () => {
  const flags = {
    MaterialSourceTraceGate: {
      verdict: 'warn',
      severity: 'advisory',
      reason: '',
      patch: { low_confidence: true },
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].key, 'material_low_confidence')
  assert.equal(badges[0].severity, 'normal')
})

test('TableFieldFillRateGate: 填充率低于阈值生成百分比 label', () => {
  const flags = {
    TableFieldFillRateGate: {
      verdict: 'warn',
      severity: 'advisory',
      reason: 'table fill rate 42% < 80%',
      patch: { table_fill_rate: 0.42, failure_state: 'needs_review' },
      trace: { fill_rate: 0.42 },
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges.length, 1)
  assert.ok(badges[0].label.includes('42%'))
  assert.equal(badges[0].key, 'table_fill_rate')
  assert.equal(badges[0].severity, 'normal')
})

test('DeAIFilterGate: AI 套话数量触发 label', () => {
  const flags = {
    DeAIFilterGate: {
      verdict: 'warn',
      severity: 'advisory',
      reason: 'ai_phrase_count=15 > 10',
      patch: {},
      trace: { ai_phrase_count: 15 },
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].key, 'ai_phrase_count')
  assert.ok(badges[0].label.includes('15'))
  assert.equal(badges[0].severity, 'normal')
})

test('FixedTemplatePlaceholderGate: 固定模板占位未替换', () => {
  const flags = {
    FixedTemplatePlaceholderGate: {
      verdict: 'warn',
      severity: 'enforce',
      reason: 'fixed_template_placeholder node_type=fixed_template 存在 [待补充:公司名称]',
      patch: {},
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].key, 'fixed_template_placeholder')
  assert.equal(badges[0].severity, 'normal')
  assert.ok(badges[0].hint.includes('[待补充:公司名称]'))
  assert.doesNotMatch(badges[0].hint, /fixed_template|node_type/)
})

test('WriterCoVe: 不暴露 trigger/outcome 技术字段', () => {
  const flags = {
    WriterCoVe: {
      verdict: 'disagree',
      severity: 'advisory',
      reason: 'trigger=writer_commitment; outcome=disagree',
      patch: {},
      trace: { trigger: 'writer_commitment', outcome: 'disagree' },
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges.length, 1)
  assert.equal(badges[0].label, '待复核')
  assert.equal(badges[0].severity, 'normal')
  assert.equal(badges[0].hint, '承诺内容与约束或素材存在不一致，请人工核对。')
  assert.ok(!badges[0].hint.includes('trigger='))
  assert.ok(!badges[0].hint.includes('outcome='))
})

test('WriterCoVe: 不确定结论映射为中文人工核验提示', () => {
  const flags = {
    WriterCoVe: {
      verdict: 'inconclusive',
      severity: 'advisory',
      reason: 'trigger=writer_commitment; outcome=inconclusive',
      patch: {},
      trace: { outcome: 'inconclusive' },
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].hint, '承诺内容核验不确定，请人工核对。')
})

test('fixed_template_requires_review: 短中文 label + high severity + 透传后端 reason', () => {
  // 场景一: 缺少可回查原文
  const flags1 = {
    fixed_template_requires_review: {
      verdict: 'reject',
      severity: 'high',
      reason: '固定模板缺少可回查原文，必须人工补充。',
      patch: {},
      trace: { outcome: 'needs_review' },
    },
  }
  const badges1 = gateFlagsToBadges(flags1)
  assert.equal(badges1.length, 1)
  assert.equal(badges1[0].key, 'fixed_template_requires_review')
  assert.equal(badges1[0].label, '需人工复核')
  assert.equal(badges1[0].severity, 'high')
  assert.equal(badges1[0].hint, '固定模板缺少可回查原文，必须人工补充。')

  // 场景二: 快照不完整, hint 同样透传后端 reason 区分场景
  const flags2 = {
    fixed_template_requires_review: {
      verdict: 'reject',
      severity: 'high',
      reason: '固定模板来源快照可能不完整，请人工核对招标原模板后再导出。',
      patch: {},
      trace: { outcome: 'needs_review' },
    },
  }
  const badges2 = gateFlagsToBadges(flags2)
  assert.equal(badges2[0].label, '需人工复核')
  assert.ok(badges2[0].hint.includes('快照可能不完整'))

  // 场景三: reason 为空时使用兜底文案, 但不暴露原始 name
  const flags3 = {
    fixed_template_requires_review: {
      verdict: 'reject',
      severity: 'high',
      patch: {},
      trace: {},
    },
  }
  const badges3 = gateFlagsToBadges(flags3)
  assert.equal(badges3[0].label, '需人工复核')
  assert.ok(!badges3[0].hint.includes('fixed_template_requires_review'))
})

test('pass verdict 的未知 gate 不产生 badge', () => {
  const flags = {
    SomeNewGate: { verdict: 'pass', severity: 'advisory', reason: '', patch: {}, trace: {} },
    some_new_review: { verdict: 'pass', severity: 'advisory', reason: '', patch: {}, trace: {} },
  }
  assert.deepEqual(gateFlagsToBadges(flags), [])
})

test('未知 *Gate 类名风格: 统一兜底中文 label, severity 跟随后端', () => {
  const flags = {
    SomeNewGate: {
      verdict: 'warn',
      severity: 'advisory',
      reason: 'node_type=fixed_template; custom reason',
      patch: {},
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges.length, 1)
  assert.equal(badges[0].label, '待复核')
  assert.equal(badges[0].severity, 'normal')
  assert.doesNotMatch(badges[0].hint, /SomeNewGate|node_type|fixed_template/)
})

test('未知 snake_case gate: 兜底为"待复核", 不暴露原始标识符', () => {
  // 这是 的回归防御: 后端任意新增 snake_case gate_name 时,
  // 前端不得把原始字符串 (如 fixed_template_requires_review) 当作 label 显示。
  const flags = {
    some_unknown_review: {
      verdict: 'reject',
      severity: 'advisory',
      reason: '后端未来新增的告警',
      patch: {},
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges.length, 1)
  assert.equal(badges[0].label, '待复核')
  assert.notEqual(badges[0].label, 'some_unknown_review')
  assert.ok(!badges[0].label.includes('_'))
  assert.equal(badges[0].severity, 'normal')
  assert.ok(badges[0].hint.includes('后端未来新增的告警'))
})

test('未知 snake_case gate + severity=high: 兜底升级为 high 严重度', () => {
  // 后端如果再追加 high severity 的 snake_case gate, 前端 UI 也应升级红色。
  const flags = {
    another_unknown_review: {
      verdict: 'reject',
      severity: 'high',
      reason: '高风险',
      patch: {},
      trace: {},
    },
  }
  const badges = gateFlagsToBadges(flags)
  assert.equal(badges[0].label, '待复核')
  assert.equal(badges[0].severity, 'high')
})

test('hasAnyGateWarning 正确反映告警存在性', () => {
  assert.equal(hasAnyGateWarning(null), false)
  assert.equal(hasAnyGateWarning({}), false)
  assert.equal(
    hasAnyGateWarning({
      DeAIFilterGate: { verdict: 'pass', patch: {}, trace: {} },
    }),
    false,
  )
  assert.equal(
    hasAnyGateWarning({
      DeAIFilterGate: {
        verdict: 'warn', patch: {}, trace: { ai_phrase_count: 15 },
      },
    }),
    true,
  )
  assert.equal(
    hasAnyGateWarning({
      fixed_template_requires_review: {
        verdict: 'reject', severity: 'high', reason: '缺少可回查原文', patch: {}, trace: {},
      },
    }),
    true,
  )
})

test('summarizeGateWarnings 汇总章节数、高风险数与首条原因', () => {
  const summary = summarizeGateWarnings([
    {
      nodeId: 1,
      title: '目录',
      gateFlags: {
        fixed_template_requires_review: {
          verdict: 'reject', severity: 'high', reason: '固定模板缺少可回查原文', patch: {}, trace: {},
        },
      },
    },
    {
      nodeId: 2,
      title: '质量认证证书',
      gateFlags: {
        TableFieldFillRateGate: {
          verdict: 'warn', severity: 'advisory', reason: '', patch: { table_fill_rate: 0.42 }, trace: {},
        },
      },
    },
  ])

  assert.equal(summary.total, 2)
  assert.equal(summary.high, 1)
  assert.equal(summary.items[0].nodeId, 1)
  assert.equal(summary.items[0].badges[0].label, '需人工复核')
})

test('gateFlagsToBadges 按重要度排序, high badge 优先作为首条展示', () => {
  const badges = gateFlagsToBadges({
    MaterialSourceTraceGate: {
      verdict: 'warn',
      severity: 'advisory',
      reason: '',
      patch: { low_confidence: true },
      trace: {},
    },
    fixed_template_requires_review: {
      verdict: 'reject',
      severity: 'high',
      reason: '固定模板缺少可回查原文',
      patch: {},
      trace: {},
    },
  })

  assert.equal(badges.length, 2)
  assert.equal(badges[0].key, 'fixed_template_requires_review')
  assert.equal(badges[0].severity, 'high')
  assert.equal(badges[1].key, 'material_low_confidence')
})

test('summarizeGateWarnings 按章节最高风险与业务重要度排序且同级稳定', () => {
  const summary = summarizeGateWarnings([
    {
      nodeId: 10,
      title: 'AI 表达检查',
      gateFlags: {
        DeAIFilterGate: {
          verdict: 'warn',
          severity: 'advisory',
          reason: '',
          patch: {},
          trace: { ai_phrase_count: 12 },
        },
      },
    },
    {
      nodeId: 20,
      title: '固定模板',
      gateFlags: {
        FixedTemplatePlaceholderGate: {
          verdict: 'warn',
          severity: 'advisory',
          reason: '存在 [待补充:公司名称]',
          patch: {},
          trace: {},
        },
      },
    },
    {
      nodeId: 30,
      title: '固定模板来源',
      gateFlags: {
        fixed_template_requires_review: {
          verdict: 'reject',
          severity: 'high',
          reason: '固定模板缺少可回查原文',
          patch: {},
          trace: {},
        },
      },
    },
    {
      nodeId: 40,
      title: '承诺内容',
      gateFlags: {
        WriterCoVe: {
          verdict: 'inconclusive',
          severity: 'advisory',
          reason: '',
          patch: {},
          trace: { outcome: 'inconclusive' },
        },
      },
    },
    {
      nodeId: 11,
      title: 'AI 表达复查',
      gateFlags: {
        DeAIFilterGate: {
          verdict: 'warn',
          severity: 'advisory',
          reason: '',
          patch: {},
          trace: { ai_phrase_count: 8 },
        },
      },
    },
  ])

  assert.deepEqual(
    summary.items.map((item) => item.nodeId),
    [30, 20, 40, 10, 11],
  )
})
