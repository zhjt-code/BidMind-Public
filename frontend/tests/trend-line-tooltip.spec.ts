import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { formatTrendTooltipValue } from '../src/pages/admin/ops/components/trendTooltipFormat.ts'

const __dirname = dirname(fileURLToPath(import.meta.url))
const trendLinePath = join(
  __dirname,
  '../src/pages/admin/ops/components/TrendLine.tsx',
)

test('formatTrendTooltipValue 正常数值带单位', () => {
  assert.deepEqual(formatTrendTooltipValue(12.5, 'ms', '延迟'), ['12.5 ms', '延迟'])
})

test('formatTrendTooltipValue 无单位时保留原数值类型', () => {
  assert.deepEqual(formatTrendTooltipValue(3, undefined, '次数'), [3, '次数'])
})

test('formatTrendTooltipValue 兼容 undefined/null（recharts Formatter）', () => {
  assert.deepEqual(formatTrendTooltipValue(undefined, '%'), ['—', '数值'])
  assert.deepEqual(formatTrendTooltipValue(null, '%', '成功率'), ['—', '成功率'])
})

test('TrendLine Tooltip formatter 禁止把 value 窄化为 number', () => {
  const source = readFileSync(trendLinePath, 'utf8')
  assert.doesNotMatch(
    source,
    /formatter=\{\(value:\s*number\)/,
    'recharts 3.x Formatter 入参含 undefined，窄化为 number 会导致 tsc -b 失败',
  )
  assert.match(source, /formatTrendTooltipValue\(value,\s*unit,\s*yLabel\)/)
})
