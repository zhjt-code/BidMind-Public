/**
 * WritingCompose 余额门禁契约测试 ( Wave 2-F)
 *
 * 源码契约（不引 React 渲染）:
 * 1. 页面接入 useInsufficientBalanceGate + BalanceGateCard，文案取自集中注册表
 * 2. 单章门禁 nodeKey 按章节动态隔离 (writing_compose_single:${currentNodeId})
 * 3. 自动全文生成 effect 被 isBlocked 截断（含刷新后 sessionStorage 恢复的阻断），
 * 阻断时不置 autoTriggeredRef，充值清除后可恢复自动
 * 4. capture 覆盖 handleCompose/handleRegenerate/retryCompose/retrySingle 的 catch 路径，
 * 命中后跳过通用 toast 与 retryState 拉取/刷新（余额失败不是任务失败）
 * 5. 成功路径 loadDrafts 之后调用对应 gate.clear()
 * 6. 充值后继续: 全文有重试上下文走 retryCompose，否则 handleCompose(true)；
 * 单章为 clear + handleRegenerate
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const src = readFileSync(
  new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url),
  'utf8',
)

/** 提取组件顶层 useCallback 定义体（从声明到同缩进层的 `}, [deps])` 结束） */
function callbackSource(name: string): string {
  const start = src.indexOf(`const ${name} = useCallback`)
  assert.notEqual(start, -1, `未找到 ${name} 定义`)
  const bodyEnd = src.indexOf('\n  }, [', start)
  assert.notEqual(bodyEnd, -1, `未找到 ${name} 结束`)
  return src.slice(start, src.indexOf('])', bodyEnd) + 2)
}

test('页面接入共享门禁 hook 与卡片组件，文案取自注册表', () => {
  assert.match(src, /useInsufficientBalanceGate\(\{/)
  assert.match(src, /<BalanceGateCard/)
  assert.match(src, /getBalanceGateCopy\('writing_compose'\)/)
  assert.match(src, /getBalanceGateCopy\('writing_compose_single'\)/)
  assert.match(src, /'writing_retry'/)
  assert.match(src, /paymentEnabled: isPaymentEnabled/)
})

test('单章门禁 nodeKey 按章节动态隔离', () => {
  assert.match(src, /writing_compose_single:\$\{currentNodeId\}/)
})

test('自动全文生成 effect 被 isBlocked 截断且阻断时不置 autoTriggeredRef', () => {
  const condIdx = src.indexOf("isAtLeast(projectStatus, 'matched')")
  assert.notEqual(condIdx, -1, '未找到自动触发 effect 条件')
  const effectStart = src.lastIndexOf('useEffect', condIdx)
  const effect = src.slice(effectStart, src.indexOf('])', condIdx) + 2)
  assert.match(effect, /!composeGate\.isBlocked/)
  // isBlocked 是进入条件: 阻断时整块跳过, autoTriggeredRef 不被置位
  assert.ok(
    effect.indexOf('!composeGate.isBlocked') < effect.indexOf('autoTriggeredRef.current = true'),
    'isBlocked 判断必须先于 autoTriggeredRef 置位',
  )
  const deps = (effect.match(/composeGate\.isBlocked/g) || []).length
  assert.ok(deps >= 2, '依赖列表必须包含 composeGate.isBlocked')
})

test('handleCompose: catch 命中 capture 跳过通用 toast 与 retryState 拉取, 成功后 clear', () => {
  const fn = callbackSource('handleCompose')
  assert.match(fn, /catch \(e\)[\s\S]*if \(!composeGate\.capture\(e\)\)/)
  const guardIdx = fn.indexOf('!composeGate.capture(e)')
  assert.ok(guardIdx < fn.indexOf("getRetryState(pid, 'writing_compose')"), 'retryState 拉取必须在未命中分支内')
  assert.ok(guardIdx < fn.indexOf('toast.error'), '通用 toast 必须在未命中分支内')
  const loadIdx = fn.indexOf('await loadDrafts(pid)')
  const clearIdx = fn.indexOf('composeGate.clear()')
  assert.ok(loadIdx !== -1 && clearIdx > loadIdx, '成功路径 loadDrafts 后必须 clear')
})

test('retryCompose: capture 命中跳过通用 toast 与 retryState 刷新, 成功后 clear', () => {
  const fn = callbackSource('retryCompose')
  assert.match(fn, /catch \(e\)[\s\S]*if \(!composeGate\.capture\(e\)\)/)
  assert.ok(
    fn.indexOf('!composeGate.capture(e)') < fn.indexOf('refreshComposeRetryState().catch'),
    'retryState 刷新必须在未命中分支内',
  )
  assert.ok(fn.indexOf('composeGate.clear()') > fn.indexOf('await loadDrafts(pid)'))
})

test('handleRegenerate: capture 命中不 setSingleRetryState, 成功后 clear', () => {
  const fn = callbackSource('handleRegenerate')
  assert.match(fn, /catch \(e\)[\s\S]*if \(!singleGate\.capture\(e\)\)/)
  const catchIdx = fn.indexOf('catch (e)')
  const guardIdx = fn.indexOf('!singleGate.capture(e)', catchIdx)
  const setIdx = fn.indexOf('setSingleRetryState', catchIdx)
  assert.ok(guardIdx !== -1 && setIdx > guardIdx, 'catch 内 setSingleRetryState 必须在 capture 守卫之后')
  assert.ok(fn.indexOf('singleGate.clear()') > fn.indexOf('await loadDrafts(pid)'))
})

test('retrySingle: capture 命中跳过通用 toast 与 retryState 刷新, 成功后 clear', () => {
  const fn = callbackSource('retrySingle')
  assert.match(fn, /catch \(e\)[\s\S]*if \(!singleGate\.capture\(e\)\)/)
  assert.ok(
    fn.indexOf('!singleGate.capture(e)') < fn.indexOf('refreshSingleRetryState().catch'),
    'retryState 刷新必须在未命中分支内',
  )
  assert.ok(fn.indexOf('singleGate.clear()') > fn.indexOf('await loadDrafts(pid)'))
})

test('渲染: 无草稿阻断为 center 完成卡, 有草稿为 inline, 单章卡在编辑区上方', () => {
  assert.match(src, /composeGate\.blocked && drafts\.length === 0 \?[\s\S]{0,120}layout="center"/)
  assert.match(src, /composeGate\.blocked && drafts\.length > 0 &&[\s\S]{0,120}layout="inline"/)
  // 有草稿时语义随重试上下文切换, 文案对应 writing_retry / writing_compose
  assert.match(src, /semantic=\{composeRetryState \? 'retry' : 'completed'\}/)
  assert.match(src, /getBalanceGateCopy\(composeRetryState \? 'writing_retry' : 'writing_compose'\)/)
  assert.match(src, /singleGate\.blocked &&[\s\S]{0,120}layout="inline"/)
  assert.match(src, /singleGate\.blocked &&[\s\S]{0,200}semantic="retry"/)
})

test('充值后继续动作: 全文按重试上下文分流, 单章 clear+handleRegenerate', () => {
  // 全文继续: 先 clear, 有 composeRetryState 走 retryCompose, 否则 handleCompose(true)
  assert.match(
    src,
    /composeGate\.clear\(\)[\s\S]{0,160}retryCompose\(\)[\s\S]{0,80}handleCompose\(true\)/,
  )
  // 单章继续: clear 后立即 handleRegenerate
  assert.match(src, /singleGate\.clear\(\)\s*\n\s*handleRegenerate\(\)/)
  // 草稿/编辑器在阻断态保留: 单章卡与编辑器同分支共存, 不整页替换
  assert.match(src, /RichTextEditor/)
})

test('余额阻断时全文/单章 RetryActionPanel 均须隐藏', () => {
  const composeRetryOpen = src.indexOf("composeRetryState?.failedNode === 'writing_compose'")
  assert.ok(composeRetryOpen >= 0, '应存在全文失败 RetryActionPanel 条件')
  const composeRetryCond = src.slice(
    composeRetryOpen,
    src.indexOf('<RetryActionPanel', composeRetryOpen),
  )
  assert.match(composeRetryCond, /!composeGate\.isBlocked/, '全文阻断时必须隐藏 RetryActionPanel')

  const singleRetryOpen = src.indexOf("singleRetryState?.failedNode === 'writing_compose_single'")
  assert.ok(singleRetryOpen >= 0, '应存在单章失败 RetryActionPanel 条件')
  const singleRetryCond = src.slice(
    singleRetryOpen,
    src.indexOf('<RetryActionPanel', singleRetryOpen),
  )
  assert.match(singleRetryCond, /!singleGate\.isBlocked/, '单章阻断时必须隐藏 RetryActionPanel')
})
