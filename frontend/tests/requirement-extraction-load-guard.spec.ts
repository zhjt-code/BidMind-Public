/**
 * 防御测试：确认页首载不得把「重新提取进行中」的旧清单当完成页。
 *
 * 职责: 锁定 extracting/running 必须留在提取进度视图。
 * 不做: 不挂载 React、不调用真实 API。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { shouldHoldExtractionProgressView } from '../src/pages/RequirementReview/hooks/extractionLoadGuard.ts'

const here = dirname(fileURLToPath(import.meta.url))

test('running 状态必须留在提取进度视图', () => {
  assert.equal(shouldHoldExtractionProgressView('running', 'extracted'), true)
})

test('项目 EXTRACTING 即使状态接口仍报 completed 也必须留在进度视图', () => {
  assert.equal(shouldHoldExtractionProgressView('completed', 'extracting'), true)
  assert.equal(shouldHoldExtractionProgressView('idle', 'extracting'), true)
})

test('已提取完成且项目非 extracting 才允许直出确认清单', () => {
  assert.equal(shouldHoldExtractionProgressView('completed', 'extracted'), false)
  assert.equal(shouldHoldExtractionProgressView('idle', 'extracted'), false)
})

test('RequirementReview 首载必须消费 extracting 护栏', () => {
  const source = readFileSync(
    join(here, '../src/pages/RequirementReview/index.tsx'),
    'utf8',
  )
  assert.match(source, /shouldHoldExtractionProgressView/)
  assert.match(source, /refreshProjectStatus|getProject/)
})
