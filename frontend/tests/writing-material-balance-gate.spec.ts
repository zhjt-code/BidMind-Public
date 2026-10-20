/**
 * 写标素材批处理余额门禁契约测试 ( Wave 2-D)
 *
 * 冻结契约:
 * 1. useWritingMaterialsPolling.startBatchProcess: 余额 403 停轮询、退 processing 态并
 * 经 onBalanceBlocked 上抛；瞬态超时保持静默（catch 不再纯空吞但不误伤超时）
 * 2. useWritingMaterialsRetry.handleRetryMaterialProcess: 余额命中走 onBalanceBlocked
 * 并跳过通用失败 toast；未命中保持原提示
 * 3. index.tsx: 建 useInsufficientBalanceGate(nodeKey=writing_material_process)、
 * 两个 hook 均接入 gate.capture、blocked 时渲染 BalanceGateCard、
 * 上传完成与自动恢复轮询双路径截断自动推进、onContinue 先 clear 再启动
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { getBalanceGateCopy } from '../src/lib/balanceGateCopy.ts'
import { parseInsufficientBalanceError } from '../src/lib/insufficientBalance.ts'

const pollingSrc = readFileSync(
  new URL('../src/pages/writing/WritingMaterials/hooks/useWritingMaterialsPolling.ts', import.meta.url),
  'utf8',
)
const retrySrc = readFileSync(
  new URL('../src/pages/writing/WritingMaterials/hooks/useWritingMaterialsRetry.ts', import.meta.url),
  'utf8',
)
const pageSrc = readFileSync(
  new URL('../src/pages/writing/WritingMaterials/index.tsx', import.meta.url),
  'utf8',
)

/** 截取 [startMark, endMark) 之间的源码块，断言其存在 */
function sliceBlock(src: string, startMark: string, endMark: string): string {
  const start = src.indexOf(startMark)
  const end = src.indexOf(endMark, start + startMark.length)
  assert.ok(start >= 0, `源码中应存在标记: ${startMark}`)
  assert.ok(end > start, `源码中应存在结束标记: ${endMark}`)
  return src.slice(start, end)
}

// 1. 轮询 hook: startBatchProcess 余额分流 ----------

test('polling: startBatchProcess 余额 403 阻断上抛，catch 不再纯空吞', () => {
  assert.match(pollingSrc, /onBalanceBlocked\?: \(error: unknown\) => void/)
  assert.match(pollingSrc, /parseInsufficientBalanceError/)

  const block = sliceBlock(pollingSrc, 'const startBatchProcess', 'const hasActivePoll')
  assert.match(block, /catch \(e\)/, 'catch 必须绑定错误对象，不得整体空吞')
  assert.match(block, /parseInsufficientBalanceError\(e\)/, '必须先识别余额错误再分流')
  assert.match(block, /cleanupPoll\(\)/, '余额命中必须停止轮询')
  assert.match(block, /setIsProcessing\(false\)/, '余额命中必须退出 processing 态')
  assert.match(block, /onBalanceBlockedRef\.current\?\.\(e\)|onBalanceBlocked\?\.\(e\)/, '余额命中必须上抛阻断回调')
  assert.doesNotMatch(block, /toast\.error/, '瞬态超时路径保持静默，startBatchProcess 内不得新增通用 toast')
})

// 2. 重试 hook: 余额命中跳过通用 toast ----------

test('retry: handleRetryMaterialProcess 余额命中走回调并跳过通用 toast', () => {
  assert.match(retrySrc, /onBalanceBlocked\?: \(error: unknown\) => void/)

  const block = sliceBlock(retrySrc, 'const handleRetryMaterialProcess', 'return {')
  assert.match(block, /parseInsufficientBalanceError\(e\)/)
  assert.match(block, /onBalanceBlocked\(e\)/, '余额命中必须调用阻断回调')
  assert.match(block, /toast\.error\('重试启动失败'\)/, '非余额错误保持原通用提示')
  assert.ok(
    block.indexOf('toast.success') < block.indexOf('onRetryStart()'),
    'onRetryStart 必须保持在成功启动（toast.success）之后，页面依赖该时机做 gate.clear',
  )
})

// 3. 页面: 门禁装配 + 截断自动推进 + 继续动作契约 ----------

test('页面: 建门禁并把 capture 接入轮询与重试两个 hook', () => {
  assert.match(pageSrc, /useInsufficientBalanceGate/)
  assert.match(pageSrc, /nodeKey: 'writing_material_process'/)
  assert.match(pageSrc, /paymentEnabled: isPaymentEnabled/)
  assert.equal(
    (pageSrc.match(/onBalanceBlocked: gate\.capture/g) ?? []).length,
    2,
    '轮询与重试两个 hook 都必须接入 gate.capture',
  )
})

test('页面: blocked 时渲染 inline BalanceGateCard，语义按 failedNode 切换', () => {
  assert.match(pageSrc, /BalanceGateCard/)
  const card = sliceBlock(pageSrc, 'gate.isBlocked && (', 'MaterialUploadDialog')
  assert.match(card, /layout="inline"/)
  assert.match(card, /failedNode \? 'retry' : 'completed'/)
  assert.match(card, /'writing_retry' : 'writing_material_process'/)
  assert.match(card, /gate\.blocked\?\.message \?\? ''/)
  assert.match(card, /onRecharge=\{gate\.openRecharge\}/)
})

test('页面: onContinue 固定契约为先 clear 再按 failedNode 分流启动', () => {
  const onContinue = sliceBlock(pageSrc, 'onContinue={', 'MaterialUploadDialog')
  const clearAt = onContinue.indexOf('gate.clear()')
  assert.ok(clearAt >= 0, 'onContinue 必须调用 gate.clear')
  assert.ok(
    clearAt < onContinue.indexOf('handleRetryMaterialProcess'),
    '必须先 clear 再启动重试',
  )
  assert.ok(
    clearAt < onContinue.indexOf('startBatchProcess'),
    '必须先 clear 再启动批处理',
  )
})

test('页面: 上传完成与自动恢复轮询双路径均被 gate.isBlocked 截断', () => {
  const upload = sliceBlock(pageSrc, 'const handleUploadComplete', 'const handleDelete')
  assert.match(upload, /if \(gate\.isBlocked\) return/, '上传完成后余额阻断中不得自动启动批处理')
  assert.ok(
    upload.indexOf('if (gate.isBlocked) return') < upload.indexOf('polling.startBatchProcess()'),
    '截断必须发生在 startBatchProcess 之前',
  )

  const autoResume = sliceBlock(pageSrc, '// 自启动轮询', '// ============ 动作回调')
  assert.match(autoResume, /!gate\.isBlocked/, '余额阻断中不得自动恢复轮询')
})

test('页面: 重试成功启动后经 onRetryStart 解除阻断', () => {
  const retryWiring = sliceBlock(pageSrc, 'onRetryStart: useCallback', 'onBalanceBlocked')
  assert.match(retryWiring, /gate\.clear\(\)/, 'onRetryStart（成功启动时机）必须清除阻断')
})

test('页面: 余额阻断时不得与 RetryActionPanel 并存', () => {
  const retryOpen = pageSrc.indexOf('{showRetryPanel && activeRetryState &&')
  assert.ok(retryOpen >= 0, '应存在 RetryActionPanel 条件渲染')
  const retryCond = pageSrc.slice(retryOpen, pageSrc.indexOf('<RetryActionPanel', retryOpen))
  assert.match(retryCond, /!gate\.isBlocked/, '阻断时必须隐藏 RetryActionPanel，只留门禁卡')
})

// 4. 纯函数行为: 文案注册与错误分流 ----------

test('文案注册: writing_material_process 与 writing_retry 两套语义齐备', () => {
  const completed = getBalanceGateCopy('writing_material_process')
  assert.equal(completed.title, '素材已保存')
  assert.equal(completed.continueLabel, '继续处理')

  const retryCopy = getBalanceGateCopy('writing_retry')
  assert.match(retryCopy.title, /重试未启动/)
  assert.equal(retryCopy.continueLabel, '继续重试')
})

test('错误分流: 结构化余额 403 命中，瞬态超时/网络错误不误伤', () => {
  const balanceError = Object.assign(new Error('余额不足，可用0, 需要400'), {
    errorCode: 'INSUFFICIENT_BALANCE',
    details: { available: 0, required: 400 },
  })
  const info = parseInsufficientBalanceError(balanceError)
  assert.ok(info, '结构化余额错误必须命中')
  assert.equal(info.available, 0)
  assert.equal(info.required, 400)

  // startBatchProcess 原本吞掉的典型瞬态错误必须继续走静默路径
  assert.equal(parseInsufficientBalanceError(new Error('timeout of 30000ms exceeded')), null)
  assert.equal(parseInsufficientBalanceError(new Error('Network Error')), null)
  assert.equal(parseInsufficientBalanceError(new Error('无法连接到服务器，请检查网络连接后重试')), null)
})
