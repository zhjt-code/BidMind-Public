import assert from 'node:assert/strict'
import test from 'node:test'

import { getMaterialDisplayName } from '../src/pages/writing/materialDisplay.ts'

test('素材展示名优先使用 effectiveTitle', () => {
  assert.equal(
    getMaterialDisplayName({
      title: '196',
      displayTitle: '劳动合同扫描件',
      effectiveTitle: '劳动合同扫描件2',
    }),
    '劳动合同扫描件2',
  )
})

test('effectiveTitle 为空白时回退 displayTitle', () => {
  assert.equal(
    getMaterialDisplayName({
      title: '196',
      displayTitle: '劳动合同扫描件',
      effectiveTitle: '   ',
    }),
    '劳动合同扫描件',
  )
})

test('displayTitle 缺失时回退原始 title', () => {
  assert.equal(
    getMaterialDisplayName({
      title: '196',
      displayTitle: null,
      effectiveTitle: '',
    }),
    '196',
  )
})
