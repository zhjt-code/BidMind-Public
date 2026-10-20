/**
 * 余额门禁共享层契约测试 
 *
 * 冻结契约（Wave 1 实现前必须失败，实现后必须全绿）:
 * 1. parseInsufficientBalanceError: 结构化 errorCode 优先，旧中文文案 fallback，
 * 网络/权限/进行中错误一律 null
 * 2. sessionStorage 快照: 按 projectId+nodeKey 隔离读写清除，损坏 JSON 安全返回 null
 * 3. BalanceGateCard: layout('center'|'inline') 与 semantic('completed'|'retry') 分离，
 * 支付关闭隐藏充值
 * 4. useInsufficientBalanceGate: capture/clear/blocked + openRechargeDialog
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  INSUFFICIENT_BALANCE_GATE_HINT,
  INSUFFICIENT_BALANCE_TOAST,
} from '../src/lib/balanceGateCopy.ts'
import {
  INSUFFICIENT_BALANCE_ERROR_CODE,
  balanceGateStorageKey,
  clearBalanceGateSnapshot,
  computeCreditShortfall,
  isInsufficientBalanceError,
  parseInsufficientBalanceError,
  readBalanceGateSnapshot,
  writeBalanceGateSnapshot,
  type BalanceGateSnapshot,
} from '../src/lib/insufficientBalance.ts'

// 1. 错误解析 ----------

test('结构化 errorCode 优先识别并提取数值', () => {
  const error = Object.assign(new Error('余额不足，可用0, 需要400'), {
    errorCode: 'INSUFFICIENT_BALANCE',
    details: { available: 0, required: 400 },
  })
  const info = parseInsufficientBalanceError(error)
  assert.ok(info)
  assert.equal(info.message, '余额不足，可用0, 需要400')
  assert.equal(info.available, 0)
  assert.equal(info.required, 400)
  assert.equal(INSUFFICIENT_BALANCE_ERROR_CODE, 'INSUFFICIENT_BALANCE')
})

test('旧中文文案 fallback 仍可识别并解析数值', () => {
  const info = parseInsufficientBalanceError(new Error('启动失败: 余额不足，可用3, 需要10'))
  assert.ok(info)
  assert.equal(info.available, 3)
  assert.equal(info.required, 10)
})

test('文案无数值时 available/required 为 null', () => {
  const info = parseInsufficientBalanceError(new Error('余额不足'))
  assert.ok(info)
  assert.equal(info.available, null)
  assert.equal(info.required, null)
})

test('非余额错误一律返回 null', () => {
  assert.equal(parseInsufficientBalanceError(new Error('无法连接到服务器，请检查网络连接后重试')), null)
  assert.equal(parseInsufficientBalanceError(new Error('权限不足')), null)
  assert.equal(parseInsufficientBalanceError(new Error('HTTP异常: 400 - 串标分析正在进行中，请勿重复启动')), null)
  assert.equal(parseInsufficientBalanceError(''), null)
  assert.equal(parseInsufficientBalanceError(null), null)
  assert.equal(parseInsufficientBalanceError(undefined), null)
})

test('isInsufficientBalanceError 兼容旧字符串签名', () => {
  assert.equal(isInsufficientBalanceError('余额不足，可用0, 需要400'), true)
  assert.equal(isInsufficientBalanceError('无法连接到服务器'), false)
})

test('computeCreditShortfall: 差额=需要-可用，缺 available 当 0，缺 required 为 null', () => {
  assert.equal(computeCreditShortfall({ available: 0, required: 116 }), 116)
  assert.equal(computeCreditShortfall({ available: 50, required: 116 }), 66)
  assert.equal(computeCreditShortfall({ available: null, required: 116 }), 116)
  assert.equal(computeCreditShortfall({ available: 0, required: null }), null)
  assert.equal(computeCreditShortfall({ available: 200, required: 116 }), null)
})

// 2. sessionStorage 快照 ----------

function mockStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() { return map.size },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => { map.delete(k) },
    setItem: (k: string, v: string) => { map.set(k, v) },
  } as Storage
}

test('快照按 projectId+nodeKey 隔离读写', () => {
  const storage = mockStorage()
  const snapshot: BalanceGateSnapshot = {
    projectId: 7,
    nodeKey: 'writing_extract',
    message: '余额不足，可用0, 需要400',
    available: 0,
    required: 400,
  }
  writeBalanceGateSnapshot(snapshot, storage)

  const read = readBalanceGateSnapshot(7, 'writing_extract', storage)
  assert.deepEqual(read, snapshot)
  assert.equal(readBalanceGateSnapshot(7, 'writing_compose', storage), null, '不同 nodeKey 隔离')
  assert.equal(readBalanceGateSnapshot(8, 'writing_extract', storage), null, '不同 projectId 隔离')
})

test('清除后读取为 null', () => {
  const storage = mockStorage()
  writeBalanceGateSnapshot({
    projectId: 7, nodeKey: 'review_start', message: '余额不足', available: null, required: null,
  }, storage)
  clearBalanceGateSnapshot(7, 'review_start', storage)
  assert.equal(readBalanceGateSnapshot(7, 'review_start', storage), null)
})

test('损坏 JSON 安全返回 null 且不抛错', () => {
  const storage = mockStorage()
  storage.setItem(balanceGateStorageKey(7, 'writing_extract'), '{broken json')
  assert.equal(readBalanceGateSnapshot(7, 'writing_extract', storage), null)
})

test('无 storage 环境（SSR/隐私模式）安全空跑', () => {
  assert.equal(readBalanceGateSnapshot(1, 'any', undefined), null)
  assert.doesNotThrow(() => writeBalanceGateSnapshot({
    projectId: 1, nodeKey: 'any', message: 'x', available: null, required: null,
  }, undefined))
  assert.doesNotThrow(() => clearBalanceGateSnapshot(1, 'any', undefined))
})

// 3. BalanceGateCard 契约（源码） ----------

test('BalanceGateCard: layout 与 semantic 分离，支付关闭隐藏充值', () => {
  const src = readFileSync(new URL('../src/components/BalanceGateCard.tsx', import.meta.url), 'utf8')
  assert.match(src, /'center' \| 'inline'/)
  assert.match(src, /'completed' \| 'retry'/)
  assert.match(src, /paymentEnabled/)
  assert.match(src, /去充值/)
  assert.match(src, /联系管理员/)
})

test('BalanceGateCard 展示定性提示，不渲染预占可用/需要数字', () => {
  const src = readFileSync(new URL('../src/components/BalanceGateCard.tsx', import.meta.url), 'utf8')
  assert.match(src, /INSUFFICIENT_BALANCE_GATE_HINT/)
  assert.doesNotMatch(src, /\{message\}/)
  assert.equal(INSUFFICIENT_BALANCE_GATE_HINT.includes('需要'), false)
  assert.equal(INSUFFICIENT_BALANCE_TOAST.includes('需要'), false)
})

// 4. useInsufficientBalanceGate 契约（源码） ----------

test('useInsufficientBalanceGate: capture/clear/blocked + 充值入口 + toast 去重', () => {
  const src = readFileSync(new URL('../src/hooks/useInsufficientBalanceGate.ts', import.meta.url), 'utf8')
  assert.match(src, /capture/)
  assert.match(src, /clear/)
  assert.match(src, /blocked/)
  assert.match(src, /INSUFFICIENT_BALANCE_TOAST_ID|toastId/)
  assert.match(src, /parseInsufficientBalanceError/)
})

test('useInsufficientBalanceGate toast 用定性文案，充值传入差额', () => {
  const src = readFileSync(new URL('../src/hooks/useInsufficientBalanceGate.ts', import.meta.url), 'utf8')
  assert.match(src, /INSUFFICIENT_BALANCE_TOAST/)
  assert.doesNotMatch(src, /info\.message/)
})

test('Header 不提供账户余额入口', () => {
  const src = readFileSync(new URL('../src/components/Header.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(src, /账户中心/)
  assert.doesNotMatch(src, /wallet\.available/)
})

// 5. 文案注册表契约（源码） ----------

test('balanceGateCopy: 三流程节点文案集中注册', () => {
  const src = readFileSync(new URL('../src/lib/balanceGateCopy.ts', import.meta.url), 'utf8')
  for (const key of [
    'requirement_extract', 'review_start', 'review_retry',
    'collusion_start', 'collusion_retry',
    'writing_extract', 'writing_outline', 'writing_material_process',
    'writing_match', 'writing_compose', 'writing_compose_single',
  ]) {
    assert.match(src, new RegExp(key), `缺少节点文案: ${key}`)
  }
})
