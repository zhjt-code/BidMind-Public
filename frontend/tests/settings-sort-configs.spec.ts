import assert from 'node:assert/strict'
import test from 'node:test'

import { sortConfigs } from '../src/pages/settings/sortConfigs.ts'
import type { ModelConfigData } from '../src/services/settingsApi.ts'

function makeCfg(id: string, model_name = id): ModelConfigData {
  return {
    id,
    api_base: 'https://api.example.com',
    model_name,
    config_type: 'cloud',
    api_key_configured: true,
    api_key_preview: 'sk-...****',
    key_count: 1,
    api_keys_info: [],
    temperature: null,
  } as unknown as ModelConfigData
}

test('在调度池中的卡片按 priority 升序排在前面', () => {
  const items = [
    makeCfg('a'),
    makeCfg('b'),
    makeCfg('c'),
    makeCfg('d'),
    makeCfg('e'),
  ]
  const poolIds = ['a', 'b', 'c', 'd', 'e']
  const priority = { a: 100, b: 0, c: 999, d: 50, e: 200 }
  const sorted = sortConfigs(items, poolIds, priority)
  assert.deepEqual(sorted.map((c) => c.id), ['b', 'd', 'a', 'e', 'c'])
})

test('未加入调度池的卡片排在所有在池卡片之后', () => {
  const items = [makeCfg('a'), makeCfg('b'), makeCfg('c'), makeCfg('d')]
  const poolIds = ['c', 'd']
  const priority = { c: 100, d: 0 }
  const sorted = sortConfigs(items, poolIds, priority)
  assert.deepEqual(sorted.map((c) => c.id), ['d', 'c', 'a', 'b'])
})

test('同档内部保持原始顺序（稳定排序）', () => {
  const items = [
    makeCfg('first'),
    makeCfg('second'),
    makeCfg('third'),
  ]
  const poolIds = ['first', 'second', 'third']
  const priority = { first: 100, second: 100, third: 100 }
  const sorted = sortConfigs(items, poolIds, priority)
  assert.deepEqual(sorted.map((c) => c.id), ['first', 'second', 'third'])
})

test('未在 priority 映射中的池内卡片按默认 100 处理', () => {
  const items = [makeCfg('a'), makeCfg('b'), makeCfg('c')]
  const poolIds = ['a', 'b', 'c']
  const priority = { b: 0 }
  const sorted = sortConfigs(items, poolIds, priority)
  assert.deepEqual(sorted.map((c) => c.id), ['b', 'a', 'c'])
})

test('不修改原数组（纯函数语义）', () => {
  const items = [makeCfg('a'), makeCfg('b')]
  const original = items.map((c) => c.id)
  sortConfigs(items, ['b'], { b: 0 })
  assert.deepEqual(items.map((c) => c.id), original)
})

test('空输入返回空数组', () => {
  assert.deepEqual(sortConfigs([], [], {}), [])
})
