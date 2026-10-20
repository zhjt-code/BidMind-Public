/**
 * 写标三元匹配余额门禁契约测试 
 *
 * 冻结契约:
 * 1. useMatchPolling 暴露 balanceBlocked / onBalanceBlocked / onBalanceCleared 三输入，
 * 余额识别统一走 parseInsufficientBalanceError
 * 2. 自动触发 effect 在 balanceBlocked 期间截断且不置 autoTriggeredRef，
 * 清除阻断后仍可再次自动或手动触发
 * 3. handleMatch / handleRetryMatch 命中余额错误回调 onBalanceBlocked，不走通用失败 toast
 * 4. 成功启动（triggerMatch / retry）与匹配完成（matched 分支）均回调 onBalanceCleared
 * 5. WritingBinding 页面以 nodeKey 'writing_match' 建 gate，阻断时渲染 inline BalanceGateCard，
 * 继续动作先 clear 再按 retry 语义分流 handleRetryMatch / handleMatch
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const hookSrc = readFileSync(
  new URL('../src/pages/writing/WritingBinding/hooks/useMatchPolling.ts', import.meta.url),
  'utf8',
)
const pageSrc = readFileSync(
  new URL('../src/pages/writing/WritingBinding/index.tsx', import.meta.url),
  'utf8',
)

// 1. useMatchPolling 输入契约 ----------

test('useMatchPolling: 余额门禁三输入齐备且识别走 parseInsufficientBalanceError', () => {
  assert.match(hookSrc, /balanceBlocked\?: boolean/)
  assert.match(hookSrc, /onBalanceBlocked\?: \(error: unknown\) => void/)
  assert.match(hookSrc, /onBalanceCleared\?: \(\) => void/)
  assert.match(hookSrc, /parseInsufficientBalanceError/)
})

// 2. 自动触发截断 ----------

test('useMatchPolling: 自动触发 effect 受 balanceBlocked 截断且不置 autoTriggeredRef', () => {
  const guardIdx = hookSrc.indexOf('if (balanceBlocked) return')
  const autoSetIdx = hookSrc.indexOf('autoTriggeredRef.current = true')
  assert.ok(guardIdx >= 0, '缺少 balanceBlocked 截断守卫')
  assert.ok(autoSetIdx >= 0, '缺少自动触发置位')
  assert.ok(
    guardIdx < autoSetIdx,
    '截断守卫必须先于 autoTriggeredRef 置位，保证清除阻断后可再次自动触发',
  )
  assert.match(
    hookSrc,
    /location\.pathname, balanceBlocked\]/,
    '自动触发 effect 依赖数组必须包含 balanceBlocked',
  )
})

// 3. 余额错误捕获路径 ----------

test('useMatchPolling: handleMatch 与 handleRetryMatch 命中余额错误均回调 onBalanceBlocked', () => {
  const blockedCalls = hookSrc.match(/onBalanceBlocked\?\.\(error\)/g) ?? []
  assert.equal(blockedCalls.length, 2, '两条启动 catch 都必须回调 onBalanceBlocked')
  // 余额路径 return 提前退出，通用路径（getMatchStartErrorMessage）保持不变
  assert.match(hookSrc, /getMatchStartErrorMessage/)
})

// 4. 阻断清除时机 ----------

test('useMatchPolling: 成功启动与匹配完成均回调 onBalanceCleared', () => {
  const clearedCalls = hookSrc.match(/onBalanceCleared\?\.\(\)/g) ?? []
  assert.equal(
    clearedCalls.length,
    3,
    'triggerMatch 成功、retry 成功、matched 完成分支共 3 处必须清除阻断',
  )
})

// 5. 页面接线 ----------

test('WritingBinding 页面以 writing_match 建 gate 并渲染 inline 门禁卡', () => {
  assert.match(pageSrc, /useInsufficientBalanceGate/)
  assert.match(pageSrc, /nodeKey: 'writing_match'/)
  assert.match(pageSrc, /BalanceGateCard/)
  assert.match(pageSrc, /layout="inline"/)
  assert.match(pageSrc, /getBalanceGateCopy/)
  assert.match(pageSrc, /'writing_retry' : 'writing_match'/)
  assert.match(pageSrc, /'retry' : 'completed'/)
  assert.match(pageSrc, /isPaymentEnabled/)
  assert.match(pageSrc, /balanceBlocked: balanceGate\.isBlocked/)
  assert.match(pageSrc, /onBalanceBlocked: balanceGate\.capture/)
  assert.match(pageSrc, /onBalanceCleared: balanceGate\.clear/)
})

test('WritingBinding 继续动作先 clear 再按 retry 语义分流触发', () => {
  const clearIdx = pageSrc.indexOf('balanceGate.clear()')
  const retryCallIdx = pageSrc.indexOf('void handleRetryMatch()')
  const matchCallIdx = pageSrc.indexOf('void handleMatch()')
  assert.ok(clearIdx >= 0, '继续动作必须调用 balanceGate.clear()')
  assert.ok(retryCallIdx > clearIdx, 'retry 语义继续必须在 clear 之后触发 handleRetryMatch')
  assert.ok(matchCallIdx > clearIdx, 'completed 语义继续必须在 clear 之后触发 handleMatch')
})

test('WritingBinding 余额阻断时不得与 RetryActionPanel 并存', () => {
  const retryOpen = pageSrc.indexOf("retryState?.failedNode === 'writing_match'")
  assert.ok(retryOpen >= 0, '应存在匹配失败 RetryActionPanel 条件')
  const retryCond = pageSrc.slice(retryOpen, pageSrc.indexOf('<RetryActionPanel', retryOpen))
  assert.match(retryCond, /!balanceGate\.isBlocked/, '阻断时必须隐藏 RetryActionPanel')
})
