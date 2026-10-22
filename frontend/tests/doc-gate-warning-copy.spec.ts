import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const dialogSource = readFileSync(
  new URL('../src/components/DocGateWarningDialog.tsx', import.meta.url),
  'utf8',
)

test('文档门禁提示描述文件完整性而非判定文件是否正规', () => {
  assert.match(dialogSource, /可能不是完整的招标\/投标文件/)
  assert.doesNotMatch(dialogSource, /正规(?:的)?招标\/投标文件/)
})

test('可用性预检三种独立文案存在', () => {
  assert.match(dialogSource, /文档已加密，暂时无法读取/)
  assert.match(dialogSource, /文档无法正常打开/)
  assert.match(dialogSource, /文档内容为空/)
  assert.match(dialogSource, /encrypted/)
  assert.match(dialogSource, /corrupt/)
  assert.match(dialogSource, /empty/)
})
