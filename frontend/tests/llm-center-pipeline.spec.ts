/**
 * Phase 1 防御测试基线（R2-13）：
 * 锁定 LlmCenter/ 目录化拆分前后的契约不变量：
 * - buildFlowLanes 纯函数行为（拓扑排序 + 分层）
 * - constants 出口（FLOW_TABS / CHANNEL_COLORS / STATUS_COLORS）
 * - types 出口（ViewMode 字面量）
 * - 子模块 named export 锚点（fs.readFileSync 字符串契约，
 * 兼容尚未迁出的组件 / 已迁出的组件，Step 序列推进期间保持）
 *
 * 任一断言失败即视为拆分破坏既有行为。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { buildFlowLanes } from '../src/pages/LlmCenter/lib/buildFlowLanes.ts'
import {
  FLOW_TABS,
  CHANNEL_COLORS,
  STATUS_COLORS,
} from '../src/pages/LlmCenter/lib/constants.ts'
import type { LlmNodeSummary, LlmEdgeInfo } from '../src/services/llmCenterApi.ts'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const PAGES_DIR = resolve(__dirname, '../src/pages')

const makeNode = (key: string, overrides: Partial<LlmNodeSummary> = {}): LlmNodeSummary => ({
  node_key: key,
  name: key,
  description: null,
  visibility: 'main',
  editable_level: 'advanced',
  execution_channel: 'review',
  call_method: 'call',
  default_temperature: 0,
  default_max_tokens: 1024,
  active_version: 1,
  total_versions: 1,
  param_count: { input: 0, output: 0, runtime: 0 },
  has_code_update: false,
  has_draft: false,
  last_published_at: null,
  recent_error_rate: null,
  ...overrides,
})

const makeEdge = (from: string, to: string): LlmEdgeInfo => ({
  from_node_key: from,
  to_node_key: to,
  edge_type: 'data_flow',
  param_mapping: null,
  description: null,
})

// ===== A. buildFlowLanes 纯函数契约（4 条）=====

test('buildFlowLanes 空 nodes 与 空 edges 返回 []', () => {
  assert.deepEqual(buildFlowLanes([], []), [])
})

test('buildFlowLanes 线性 edges A→B→C 拓扑分层为 3 个 lane', () => {
  const a = makeNode('A')
  const b = makeNode('B')
  const c = makeNode('C')
  const lanes = buildFlowLanes([c, a, b], [makeEdge('A', 'B'), makeEdge('B', 'C')])
  assert.equal(lanes.length, 3, '线性 3 节点应返回 3 个 lane')
  assert.equal(lanes[0]![0]!.node_key, 'A')
  assert.equal(lanes[1]![0]!.node_key, 'B')
  assert.equal(lanes[2]![0]!.node_key, 'C')
})

test('buildFlowLanes 仅过滤节点集合内的 edges（节点集外 edges 不影响 in-degree）', () => {
  const a = makeNode('A')
  const b = makeNode('B')
  // unrelated edge X→A: X 不在 nodes 中, 不应增加 A 的 in-degree
  const lanes = buildFlowLanes([a, b], [makeEdge('X', 'A'), makeEdge('A', 'B')])
  assert.equal(lanes.length, 2)
  assert.equal(lanes[0]![0]!.node_key, 'A', 'A in-degree 应为 0（X→A 被过滤掉）')
  assert.equal(lanes[1]![0]!.node_key, 'B')
})

test('buildFlowLanes 含环时 remaining 整组作为最后 lane（防止死循环）', () => {
  const a = makeNode('A')
  const b = makeNode('B')
  // A→B 与 B→A 形成环, 无 in-degree=0 的根
  const lanes = buildFlowLanes([a, b], [makeEdge('A', 'B'), makeEdge('B', 'A')])
  assert.equal(lanes.length, 1, '环检测后 remaining 整组打包')
  assert.equal(lanes[0]!.length, 2)
  const keys = lanes[0]!.map(n => n.node_key).sort()
  assert.deepEqual(keys, ['A', 'B'])
})

// ===== B. constants 出口契约（3 条）=====

test('FLOW_TABS 顺序为 writing / bid_review / collusion 且 label 中文匹配', () => {
  assert.equal(FLOW_TABS.length, 3)
  assert.equal(FLOW_TABS[0]!.key, 'writing')
  assert.equal(FLOW_TABS[0]!.label, '标书编写')
  assert.equal(FLOW_TABS[1]!.key, 'bid_review')
  assert.equal(FLOW_TABS[1]!.label, '投标审查')
  assert.equal(FLOW_TABS[2]!.key, 'collusion')
  assert.equal(FLOW_TABS[2]!.label, '串标审查')
})

test('CHANNEL_COLORS 含 review / vision 2 个键', () => {
  const keys = Object.keys(CHANNEL_COLORS).sort()
  assert.deepEqual(keys, ['review', 'vision'])
  assert.match(CHANNEL_COLORS['review']!, /bg-blue-100/)
  assert.match(CHANNEL_COLORS['vision']!, /bg-purple-100/)
})

test('STATUS_COLORS 含 published / draft / validated / archived 4 个键', () => {
  const keys = Object.keys(STATUS_COLORS).sort()
  assert.deepEqual(keys, ['archived', 'draft', 'published', 'validated'])
})

// ===== C. types 出口契约（1 条）=====

test('types.ts 含 ViewMode 字面量联合类型与 ConfirmDialogState 接口字符串锚点', () => {
  const src = readFileSync(
    resolve(PAGES_DIR, 'LlmCenter/types.ts'),
    'utf8',
  )
  assert.match(src, /export type ViewMode\s*=\s*'business'\s*\|\s*'technical'/)
  assert.match(src, /export interface ConfirmDialogState/)
  assert.match(src, /onConfirm:\s*\(\)\s*=>\s*void/)
})

// ===== D. 子模块 named export 锚点（4 条）=====

test('LlmCenter shim 主入口（pages/LlmCenter.tsx）保留 default export', () => {
  const src = readFileSync(resolve(PAGES_DIR, 'LlmCenter.tsx'), 'utf8')
  // 接受两种形态：
  // 1. `export default LlmCenterPage`（Step 7 前主入口仍为完整页面）
  // 2. `export { default } from './LlmCenter/index'`（Step 7 后主入口降为 shim）
  const hasDefaultExport =
    /export\s+default\s+\w+/.test(src) ||
    /export\s*\{\s*default\s*\}\s*from\s+['"]\.\/LlmCenter\/?(?:index)?['"]/.test(src)
  assert.ok(
    hasDefaultExport,
    'LlmCenter.tsx 必须保留 default export 作为 React.lazy 锚点',
  )
})

test('lib/buildFlowLanes.ts 含 export function buildFlowLanes', () => {
  const src = readFileSync(
    resolve(PAGES_DIR, 'LlmCenter/lib/buildFlowLanes.ts'),
    'utf8',
  )
  assert.match(src, /export\s+function\s+buildFlowLanes/)
})

test('lib/constants.ts 含 3 个 export const 锚点', () => {
  const src = readFileSync(
    resolve(PAGES_DIR, 'LlmCenter/lib/constants.ts'),
    'utf8',
  )
  assert.match(src, /export\s+const\s+FLOW_TABS/)
  assert.match(src, /export\s+const\s+CHANNEL_COLORS/)
  assert.match(src, /export\s+const\s+STATUS_COLORS/)
})

test('App.tsx 引用 LlmCenter lazy 路径 byte-equal', () => {
  const appPath = resolve(__dirname, '../src/App.tsx')
  const src = readFileSync(appPath, 'utf8')
  assert.match(
    src,
    /React\.lazy\(\(\)\s*=>\s*import\(['"]\.\/pages\/LlmCenter['"]\)\)/,
    'App.tsx 必须保留对 ./pages/LlmCenter 的 lazy import 路径（教训）',
  )
})

test('LlmCenter 批量发布按钮文案体现同步注册语义', () => {
  const src = readFileSync(resolve(PAGES_DIR, 'LlmCenter/index.tsx'), 'utf8')
  assert.match(
    src,
    /同步校验并发布/,
    ': 批量发布按钮必须让管理员明确知道会先同步代码注册表',
  )
})
