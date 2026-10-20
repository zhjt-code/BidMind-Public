import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { isInsufficientBalanceError } from '../src/pages/collusionUploadState.ts'

test('isInsufficientBalanceError 识别余额不足文案，忽略其它启动失败', () => {
  assert.equal(
    isInsufficientBalanceError('余额不足，可用0, 需要400'),
    true,
    '后端 403 原文应识别为余额不足',
  )
  assert.equal(
    isInsufficientBalanceError('启动失败: 余额不足，可用0, 需要400'),
    true,
    '前端包装后的启动失败文案仍应识别',
  )
  assert.equal(
    isInsufficientBalanceError('HTTP异常: 400 - 串标分析正在进行中，请勿重复启动'),
    false,
    '进行中 400 不应当成余额不足',
  )
  assert.equal(
    isInsufficientBalanceError('无法连接到服务器，请检查网络连接后重试'),
    false,
    '网络错误不应当成余额不足',
  )
  assert.equal(isInsufficientBalanceError(''), false, '空文案不应当成余额不足')
})

test('CollusionUpload 余额门禁必须走共享 gate 并截断自动启动', () => {
  const src = readFileSync(new URL('../src/pages/CollusionUpload.tsx', import.meta.url), 'utf8')
  // 共享层接管余额门禁（状态+快照+toast+充值弹窗）
  assert.match(src, /useInsufficientBalanceGate/)
  assert.match(src, /nodeKey: 'collusion_start'/)
  assert.match(src, /BalanceGateCard/)
  assert.match(src, /getBalanceGateCopy\('collusion_start'\)/)
  // 截断语义保留：自动启动门禁读取共享 gate 的阻断态
  assert.match(src, /shouldAutoStartCollusionAnalysis/)
  assert.match(src, /analysisBlockedByBalance: gate\.isBlocked/)
  // 固定 onContinue 行为：直接重试、不先 clear（失败由 capture 幂等重建阻断态，
  // 成功由启动路径内 gate.clear 收尾，卡片在请求期间保持可见）
  assert.match(src, /onContinue=\{\(\) => \{ void handleStartAnalysis\(\) \}\}/)
  assert.doesNotMatch(src, /gate\.clear\(\); void handleStartAnalysis/)
  // 旧的页面级实现必须清除干净
  assert.doesNotMatch(src, /CollusionBalanceBlockedBanner/)
  assert.doesNotMatch(src, /COLLUSION_INSUFFICIENT_BALANCE_TOAST_ID/)
})

test('旧横幅组件 CollusionBalanceBlockedBanner 文件已删除', () => {
  const bannerPath = fileURLToPath(
    new URL('../src/pages/collusionUploadParts/CollusionBalanceBlockedBanner.tsx', import.meta.url),
  )
  assert.equal(existsSync(bannerPath), false, '共享 gate 迁移后旧横幅组件不应残留')
})
