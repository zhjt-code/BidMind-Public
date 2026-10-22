/**
 * 三大流程余额不足入口契约 ( 起源， 统一为共享门禁)
 *
 * 所有用户可点击的计费入口页面必须：
 * 1. 使用共享 useInsufficientBalanceGate（识别/会话恢复/toast 去重/充值弹窗）
 * 2. 使用共享 BalanceGateCard 展示（禁止旧 InsufficientBalanceBanner 或手工横幅）
 * 3. 旧横幅组件保持已删除状态
 */
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const GATED_PAGES = [
  '../src/pages/RequirementReview/index.tsx',
  '../src/pages/CollusionUpload.tsx',
  '../src/pages/CollusionResult.tsx',
  '../src/pages/Result.tsx',
  '../src/components/GenericProgressPage.tsx',
  '../src/pages/writing/WritingUpload.tsx',
  '../src/pages/writing/WritingClauses.tsx',
  '../src/pages/writing/WritingOutline.tsx',
  '../src/pages/writing/WritingMaterials/index.tsx',
  '../src/pages/writing/WritingBinding/index.tsx',
  '../src/pages/writing/WritingCompose.tsx',
]

for (const page of GATED_PAGES) {
  test(`${page.replace('../src/', '')} 必须走共享余额门禁`, () => {
    const src = readFileSync(new URL(page, import.meta.url), 'utf8')
    assert.match(src, /useInsufficientBalanceGate/, '缺少共享门禁 hook')
    assert.match(src, /BalanceGateCard/, '缺少共享门禁卡片')
    assert.doesNotMatch(src, /InsufficientBalanceBanner/, '禁止旧横幅组件')
  })
}

test('旧横幅组件必须保持已删除', () => {
  assert.equal(
    existsSync(new URL('../src/components/InsufficientBalanceBanner.tsx', import.meta.url)),
    false,
    'InsufficientBalanceBanner.tsx 应已删除（收敛）',
  )
  assert.equal(
    existsSync(new URL('../src/pages/collusionUploadParts/CollusionBalanceBlockedBanner.tsx', import.meta.url)),
    false,
    'CollusionBalanceBlockedBanner.tsx 应已删除（收敛）',
  )
})

test('页面禁止再用中文文案 includes 手工识别余额错误', () => {
  for (const page of GATED_PAGES) {
    const src = readFileSync(new URL(page, import.meta.url), 'utf8')
    assert.doesNotMatch(
      src,
      /includes\(['"]余额不足['"]\)/,
      `${page} 存在手工文案识别，必须走 parseInsufficientBalanceError`,
    )
  }
})
