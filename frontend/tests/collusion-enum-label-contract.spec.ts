/**
 * 防御测试：串标审查前端枚举标签契约。
 *
 * 背景：英文技术参数裸出是历史反复故障点。
 * 根因是后端透传英文枚举码 / 字段路径，前端某些展示点漏映射，逐次点修无法根除。
 *
 * 本测试把"后端枚举全集"固化为契约，强制前端中文映射表一一覆盖：
 * - 后端新增 risk_pattern / 字段路径时，若前端未同步补映射，此测试立即失败。
 *
 * 后端来源（同步时必须对照）：
 * - backend/app/core/collusion/cross_field_correlator.py 的 _RULES[].risk_pattern
 * - backend/app/core/collusion/identifier_index.py 的 FIELD_* 常量
 * + cross_field_correlator.py 的 _FIELD_LABELS
 * - backend/app/core/collusion/analyzer_parts/findings.py attention_items[].dimension (D1-D8)
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  crossFieldRiskPatternLabels,
  getCrossFieldRiskPatternLabel,
  sourceFieldPathLabels,
  getSourceFieldLabel,
  getFindingDimensionLabel,
  findingDimensionLabels,
} from '../src/lib/constants.ts'
import { transformCollusionPairResultRaw } from '../src/services/collusionTransforms.ts'

const basePairRaw = {
  doc_a_id: 1,
  doc_b_id: 2,
  doc_a_name: 'A.pdf',
  doc_b_name: 'B.pdf',
  overall_risk: 'low',
  findings: [],
  analysis_mode: 'full',
  analysis_state: 'completed',
  decision_state: 'decidable',
  consistency_flags: [],
  risk_explanation: '',
}

// 后端 cross_field_correlator.py _RULES[].risk_pattern 全集（R01~R09 共 9 条）
const BACKEND_RISK_PATTERNS = [
  'author_eq_other_legal_rep',
  'author_eq_other_company',
  'author_eq_other_person',
  'creator_eq_other_company',
  'legal_rep_eq_other_personnel',
  'phone_cross_field',
  'subcontractor_is_other_bidder',
  'bank_eq_other_author',
  'email_user_eq_other_author',
]

// 后端 identifier_index.py FIELD_* 常量 + cross_field_correlator._FIELD_LABELS 全集
const BACKEND_SOURCE_FIELDS = [
  'company_info.company_name',
  'company_info.legal_representative',
  'company_info.registered_address',
  'company_info.contact_phone',
  'company_info.bank_account',
  'company_info.unified_social_credit_code',
  'personnel_list[].name',
  'personnel_list[].certificate_numbers',
  'personnel_list[].phone',
  'personnel_list[].email',
  'subcontractors',
  'metadata_info.author',
  'metadata_info.creator_tool',
  'metadata_info.producer',
  'metadata_info.title',
]

const hasChinese = (s: string) => /[\u4e00-\u9fff]/.test(s)

test('每个后端 risk_pattern 都被中文映射，且不裸出 snake_case 技术码', () => {
  for (const p of BACKEND_RISK_PATTERNS) {
    assert.ok(crossFieldRiskPatternLabels[p], `缺少 risk_pattern 中文映射: ${p}`)
    const label = getCrossFieldRiskPatternLabel(p)
    assert.notEqual(label, p, `risk_pattern 未被映射，裸出英文码: ${p}`)
    assert.ok(!label.includes('_'), `risk_pattern 映射值含下划线（疑似未中文化）: ${p} -> ${label}`)
    assert.ok(hasChinese(label), `risk_pattern 映射值应含中文: ${p} -> ${label}`)
  }
})

test('前端 risk_pattern 映射键应与后端规则集严格一致（无遗漏、无僵尸键）', () => {
  const frontendKeys = Object.keys(crossFieldRiskPatternLabels).sort()
  const backendKeys = [...BACKEND_RISK_PATTERNS].sort()
  assert.deepEqual(
    frontendKeys,
    backendKeys,
    '前端 crossFieldRiskPatternLabels 键集合必须与后端 _RULES.risk_pattern 完全一致',
  )
})

test('每个后端字段路径都被中文映射，且不裸出技术路径', () => {
  for (const f of BACKEND_SOURCE_FIELDS) {
    assert.ok(sourceFieldPathLabels[f], `缺少字段路径中文映射: ${f}`)
    const label = getSourceFieldLabel(f)
    assert.notEqual(label, f, `字段路径未被映射，裸出英文: ${f}`)
    assert.ok(
      !label.includes('.') && !label.includes('['),
      `字段路径映射值含路径符号（疑似未中文化）: ${f} -> ${label}`,
    )
    assert.ok(hasChinese(label), `字段路径映射值应含中文: ${f} -> ${label}`)
  }
})

test('前端字段路径映射键应覆盖后端全部字段路径', () => {
  for (const f of BACKEND_SOURCE_FIELDS) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(sourceFieldPathLabels, f),
      `字段路径映射缺失: ${f}`,
    )
  }
})

test('D1-D8 维度码都有中文映射（attention_items.dimension 与 finding.dimension 复用同一函数）', () => {
  for (let i = 1; i <= 8; i++) {
    const code = `D${i}`
    assert.ok(findingDimensionLabels[code], `缺少维度映射: ${code}`)
    const label = getFindingDimensionLabel(code)
    assert.notEqual(label, code, `维度未被映射: ${code}`)
    // 维度标签保留 D# 前缀便于专业用户识别，但必须带中文释义
    assert.ok(label.startsWith(code), `维度标签应保留代码前缀: ${code} -> ${label}`)
    assert.ok(hasChinese(label), `维度标签应含中文释义: ${code} -> ${label}`)
  }
})

test('未知枚举值兜底返回原值（不抛错，保证页面不崩）', () => {
  assert.equal(getCrossFieldRiskPatternLabel('unknown_rule'), 'unknown_rule')
  assert.equal(getSourceFieldLabel('unknown.field'), 'unknown.field')
  assert.equal(getFindingDimensionLabel('uncertain'), '待确认')
})

test('quality_flags 清洗：纯内部标记 flat_finding_recovered 被丢弃', () => {
  const pair = transformCollusionPairResultRaw({
    ...basePairRaw,
    quality_flags: ['flat_finding_recovered', '本对相似片段命中通用模板类别，请人工甄别'],
  })
  assert.ok(
    !pair.qualityFlags.includes('flat_finding_recovered'),
    'flat_finding_recovered 内部标记应被丢弃',
  )
  assert.equal(pair.qualityFlags.length, 1)
  assert.ok(pair.qualityFlags[0].includes('模板'))
})

test('quality_flags 清洗：cross_field:<pattern> 翻译为中文且不裸出英文码', () => {
  const pair = transformCollusionPairResultRaw({
    ...basePairRaw,
    quality_flags: ['cross_field:author_eq_other_legal_rep'],
  })
  assert.equal(pair.qualityFlags.length, 1)
  const flag = pair.qualityFlags[0]
  assert.ok(!flag.includes('cross_field'), '不应裸出 cross_field 英文前缀')
  assert.ok(!flag.includes('_'), '不应裸出 snake_case risk_pattern')
  assert.ok(flag.includes('跨字段命中'))
  assert.ok(flag.includes('PDF作者与对方法定代表人一致'))
})

test('quality_flags 清洗：未知 cross_field pattern 仍输出中文前缀（可追溯，不崩）', () => {
  const pair = transformCollusionPairResultRaw({
    ...basePairRaw,
    quality_flags: ['cross_field:some_future_rule'],
  })
  assert.equal(pair.qualityFlags.length, 1)
  assert.ok(pair.qualityFlags[0].startsWith('跨字段命中：'))
})
