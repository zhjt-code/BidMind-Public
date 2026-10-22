/**
 * : 前端用户态文案清洗防御测试。
 * 与后端 sanitize_user_facing_text 同义，覆盖真实泄漏样本。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { sanitizeUserFacingText } from '../src/services/progressStatusTransforms.ts'

const FIXTURES = [
  '补充信息中跨类别证据（technical页34-44；performance页358-359）均涉及施工工艺。预估得分：0分',
  '招标明确为否决项(is_mandatory=true)。预估得分：0分',
  '项目类型基本不相关（relevance_score<30），实质未响应否决项。',
  '跨类别证据（performance页358；staffing页327；format页2；qualification页348）均未涉及。',
  '其他类别证据（如performance页358-359专利、staffing页327等人员）均未涉及。',
  '审查失败[LLM_REVIEW_FAILURE]：模型调用超时或输出不合规，请人工复核',
]

const FORBIDDEN = [
  'technical页',
  'performance页',
  'staffing页',
  'qualification页',
  'format页',
  'is_mandatory',
  'relevance_score',
  'LLM_REVIEW_FAILURE',
]

test('sanitizeUserFacingText clears leak fixtures', () => {
  for (const raw of FIXTURES) {
    const out = sanitizeUserFacingText(raw)
    for (const marker of FORBIDDEN) {
      assert.equal(
        out.includes(marker),
        false,
        `残留 ${marker} in: ${out}`,
      )
    }
  }
})

test('sanitizeUserFacingText maps categories and expressions', () => {
  const out = sanitizeUserFacingText(
    'technical页12，is_mandatory=true，relevance_score<30',
  )
  assert.match(out, /技术要求/)
  assert.match(out, /属于否决项/)
  assert.match(out, /相关性评分低于30/)
  assert.equal(out.includes('technical'), false)
  assert.equal(out.includes('is_mandatory'), false)
  assert.equal(out.includes('relevance_score'), false)
})

test('sanitizeUserFacingText preserves estimated score phrase', () => {
  const out = sanitizeUserFacingText('结论如上。预估得分：0分')
  assert.match(out, /预估得分：0分/)
})

test('sanitizeUserFacingText is idempotent', () => {
  const raw = FIXTURES[0]
  const once = sanitizeUserFacingText(raw)
  const twice = sanitizeUserFacingText(once)
  assert.equal(once, twice)
})

test('sanitizeUserFacingText strips LLM_REVIEW_FAILURE marker', () => {
  const out = sanitizeUserFacingText(
    '审查失败[LLM_REVIEW_FAILURE]：模型调用超时或输出不合规，请人工复核',
  )
  assert.equal(out.includes('LLM_REVIEW_FAILURE'), false)
  assert.match(out, /审查失败/)
  assert.match(out, /请人工复核/)
})

test('sanitizeUserFacingText strips ledger overlay and requirement PKs', () => {
  const out = sanitizeUserFacingText(
    '投标文件第6页提供了身份证。项目证据账本已登记「法定代表人身份证复印件」（第44页，来自要求#43177、#43175），本条未发现结论与账本冲突，待复核',
  )
  assert.match(out, /第6页/)
  assert.equal(out.includes('项目证据账本'), false)
  assert.equal(out.includes('来自要求#'), false)
  assert.equal(out.includes('#43177'), false)
  assert.equal(out.includes('账本冲突'), false)
})

test('sanitizeUserFacingText strips license overlay second entity', () => {
  const out = sanitizeUserFacingText(
    '未发现开户许可证。项目证据账本已登记「营业执照」（第608页，来自要求#88、#99），本条未发现结论与账本冲突，待复核',
  )
  assert.match(out, /开户许可证/)
  assert.equal(out.includes('项目证据账本'), false)
  assert.equal(out.includes('#88'), false)
})
