import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const modalSource = readFileSync(new URL('../src/components/Modal.tsx', import.meta.url), 'utf8')
const settingsSource = readFileSync(new URL('../src/pages/Settings.tsx', import.meta.url), 'utf8')

test('公共 Modal 必须限制在视口内并让内容区独立滚动', () => {
  assert.match(modalSource, /max-h-\[calc\(100vh-2rem\)\]/)
  assert.match(modalSource, /\bflex-col\b/)
  assert.match(modalSource, /\bflex-1\b/)
  assert.match(modalSource, /\boverflow-y-auto\b/)
  assert.match(modalSource, /\boverscroll-contain\b/)
  assert.match(modalSource, /\bshrink-0\b/)
})

test('设置页调度密钥列表必须有局部滚动边界', () => {
  assert.match(settingsSource, /max-h-\[32vh\]/)
  assert.match(settingsSource, /\boverflow-y-auto\b/)
  assert.match(settingsSource, /\bsm:flex-row\b/)
})
