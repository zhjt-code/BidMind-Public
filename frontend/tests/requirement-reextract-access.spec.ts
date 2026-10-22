/**
 * 防御测试：招标要求“重新提取”入口对所有项目可操作用户可见。
 *
 * 职责: 锁定前端不再用 isAdmin 门控重新提取按钮。
 * 不做: 不启动浏览器、不调用真实 API。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const headerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/RequirementHeader.tsx', import.meta.url),
  'utf8',
)
const indexSource = readFileSync(
  new URL('../src/pages/RequirementReview/index.tsx', import.meta.url),
  'utf8',
)
const dialogSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ReextractConfirmDialog.tsx', import.meta.url),
  'utf8',
)

test('RequirementHeader 重新提取按钮不再由isAdmin门控', () => {
  assert.doesNotMatch(headerSource, /isAdmin/)
  assert.match(headerSource, /onOpenReextractDialog/)
  assert.match(headerSource, /重新提取/)
  assert.doesNotMatch(headerSource, /管理员从已解析文档重新提取招标要求/)
})

test('RequirementReview 页面不再从auth store计算isAdmin仅用于重新提取', () => {
  assert.doesNotMatch(indexSource, /const isAdmin = useAuthStore/)
  assert.doesNotMatch(indexSource, /isAdmin=\{isAdmin\}/)
})

test('重新提取确认弹窗文案不再限定管理员', () => {
  assert.doesNotMatch(dialogSource, /管理员/)
})

test('重新提取确认弹窗必须声明会清空评分项', () => {
  assert.match(dialogSource, /评分项/)
  assert.match(dialogSource, /确认清单/)
})
