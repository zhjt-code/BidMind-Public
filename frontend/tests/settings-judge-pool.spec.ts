import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('Settings 不再展示独立复审模型区块，提取池承接 CoVe/Judge', () => {
  const source = readFileSync(
    join(__dirname, '../src/pages/Settings.tsx'),
    'utf8',
  )
  assert.doesNotMatch(source, /title="复审模型"/)
  assert.doesNotMatch(source, /purpose="judge"/)
  assert.match(source, /title="提取模型"/)
  assert.match(source, /CoVe/)
  assert.match(source, /提取池为空时跳过/)
})

test('提取池能力 Tab 将 strict_schema 作为主能力并高亮', () => {
  const source = readFileSync(
    join(
      __dirname,
      '../src/pages/settings/modelConfigModal/CapabilityTab.tsx',
    ),
    'utf8',
  )
  assert.match(source, /purpose === 'extract'/)
  assert.match(source, /strict_schema/)
})
