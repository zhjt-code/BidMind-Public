/**
 * 写标前段（上传提取/约束/目录）余额门禁防御测试 。
 *
 * 行为断言: shouldAutoTriggerExtract 的 blockedByBalance 参数必须截断自动提取，
 * 且不阻断时保持原判定不变（回归护栏）。
 *
 * 源码契约: 三个页面必须迁移到共享门禁（useInsufficientBalanceGate 捕获 +
 * BalanceGateCard 渲染），WritingUpload 不得再保留手工中文文案判断。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { shouldAutoTriggerExtract } from '../src/pages/writing/writingUploadHelpers.ts'

test('shouldAutoTriggerExtract: 余额阻断时截断自动提取，不阻断时保持原判定', () => {
  // 原本会返回 true 的 raw（status=uploading + doc_completed）
  const raw = { status: 'uploading', currentStage: 'doc_completed', overallProgress: 60 }
  assert.equal(shouldAutoTriggerExtract(raw, true), false, '阻断中必须截断自动提取')
  assert.equal(shouldAutoTriggerExtract(raw, false), true, '未阻断时保持原判定')
  assert.equal(shouldAutoTriggerExtract(raw), true, '省略参数默认不阻断（兼容存量调用）')

  // 原本就返回 false 的 raw 不受参数影响
  const notReady = { status: 'uploading', currentStage: 'parsing', overallProgress: 30 }
  assert.equal(shouldAutoTriggerExtract(notReady, false), false)
  assert.equal(shouldAutoTriggerExtract(notReady, true), false)
})

test('WritingUpload 迁移到共享门禁，不再手工判断余额文案', () => {
  const src = readFileSync(new URL('../src/pages/writing/WritingUpload.tsx', import.meta.url), 'utf8')
  assert.match(src, /useInsufficientBalanceGate/, '必须使用共享门禁 hook')
  assert.match(src, /BalanceGateCard/, '阻断态必须渲染共享卡片')
  assert.match(
    src,
    /shouldAutoTriggerExtract\(raw, gateBlockedRef\.current\)/,
    '轮询必须用 ref 镜像的阻断态截断自动提取',
  )
  assert.doesNotMatch(
    src,
    /includes\('余额不足'\)/,
    '禁止保留手工中文文案判断（应由 gate.capture 统一识别）',
  )
})

test('WritingClauses 与 WritingOutline 使用 gate.capture 与 BalanceGateCard', () => {
  for (const page of ['WritingClauses', 'WritingOutline']) {
    const src = readFileSync(
      new URL(`../src/pages/writing/${page}.tsx`, import.meta.url),
      'utf8',
    )
    assert.match(src, /gate\.capture\(/, `${page} 必须经 gate.capture 捕获余额错误`)
    assert.match(src, /BalanceGateCard/, `${page} 阻断态必须渲染共享卡片`)
    assert.match(src, /gate\.clear\(\)/, `${page} 成功启动后必须清除阻断态`)
  }
})

test('WritingOutline 余额阻断时不得与 RetryActionPanel 并存', () => {
  const src = readFileSync(
    new URL('../src/pages/writing/WritingOutline.tsx', import.meta.url),
    'utf8',
  )
  const retryOpen = src.indexOf("retryState?.failedNode === 'writing_outline'")
  assert.ok(retryOpen >= 0, '应存在目录失败 RetryActionPanel 条件')
  const retryCond = src.slice(retryOpen, src.indexOf('<RetryActionPanel', retryOpen))
  assert.match(retryCond, /!gate\.isBlocked/, '阻断时必须隐藏 RetryActionPanel')
})
