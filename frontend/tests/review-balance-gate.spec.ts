/**
 * 投标审查流余额门禁接线契约测试 ( Wave 2-A)
 *
 * 源码契约风格（参照 collusion-balance-gate.spec.ts）:
 * 1. RequirementReview 接入统一门禁 hook 与卡片，移除旧 InsufficientBalanceBanner 路径
 * 2. 提取轮询 hook 暴露余额旁路（onBalanceBlocked）与自动重入截断（shouldBlockAutoStart）
 * 3. GenericProgressPage 重试路径接入门禁，Progress/CollusionProgress 注册各自 nodeKey
 * 4. Result 页 handleRetryReview 余额错误必须 capture 且成功后 clear
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const readSource = (relativePath: string): string =>
  readFileSync(new URL(relativePath, import.meta.url), 'utf8')

test('RequirementReview 接入统一门禁并移除旧余额横幅', () => {
  const src = readSource('../src/pages/RequirementReview/index.tsx')
  assert.match(src, /useInsufficientBalanceGate/)
  assert.match(src, /BalanceGateCard/)
  assert.doesNotMatch(src, /InsufficientBalanceBanner/, '旧横幅组件必须移除')
  // 三个计费节点各自隔离的门禁快照 key
  assert.match(src, /'requirement_extract'/)
  assert.match(src, /'requirement_reextract'/)
  assert.match(src, /'review_start'/)
  // 轮询 hook 接线：余额旁路 + 阻断态截断自动重入
  assert.match(src, /onBalanceBlocked/)
  assert.match(src, /shouldBlockAutoStart/)
})

test('提取轮询 hook 暴露余额旁路与自动启动截断选项', () => {
  const src = readSource(
    '../src/pages/RequirementReview/hooks/useRequirementExtractionPolling.ts',
  )
  assert.match(src, /onBalanceBlocked\?: \(error: unknown\) => void/)
  assert.match(src, /shouldBlockAutoStart\?: \(\) => boolean/)
  assert.match(src, /parseInsufficientBalanceError/)
  // 自动启动截断必须发生在 extractRequirements 调用之前
  const blockIdx = src.indexOf('shouldBlockAutoStartRef.current?.()')
  const startIdx = src.indexOf('requirementService.extractRequirements')
  assert.ok(blockIdx > -1 && startIdx > -1 && blockIdx < startIdx, '截断检查必须先于扣费启动')
})

test('GenericProgressPage 重试路径接入余额门禁', () => {
  const src = readSource('../src/components/GenericProgressPage.tsx')
  assert.match(src, /useInsufficientBalanceGate/)
  assert.match(src, /BalanceGateCard/)
  assert.match(src, /balanceGateNodeKey\?: 'review_retry' \| 'collusion_retry'/)
  // handleRetry: 成功 clear，失败 capture 兜底 toast
  const start = src.indexOf('const handleRetry')
  const block = src.slice(start, src.indexOf('useEffect', start))
  assert.match(block, /balanceGate\.clear\(\)/)
  assert.match(block, /balanceGate\.capture\(/)
})

test('Progress 与 CollusionProgress 注册各自门禁 nodeKey', () => {
  assert.match(readSource('../src/pages/Progress.tsx'), /balanceGateNodeKey: 'review_retry'/)
  assert.match(
    readSource('../src/pages/CollusionProgress.tsx'),
    /balanceGateNodeKey: 'collusion_retry'/,
  )
})

test('Result 页 handleRetryReview 余额错误 capture 且成功后 clear', () => {
  const src = readSource('../src/pages/Result.tsx')
  assert.match(src, /useInsufficientBalanceGate/)
  assert.match(src, /'review_retry'/)
  const start = src.indexOf('const handleRetryReview')
  const block = src.slice(start, src.indexOf('}, [', start))
  assert.match(block, /retryGate\.capture\(/)
  assert.match(block, /retryGate\.clear\(\)/)
})
