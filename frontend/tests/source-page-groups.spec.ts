import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import type { BidEnvelopeProfile } from '../src/lib/bidEnvelope.ts'
import { groupSourcePageLocations } from '../src/pages/result/sourcePageGroups.ts'

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const resultDetailSource = readFileSync(
  join(frontendRoot, 'src/pages/result/ResultDetail.tsx'),
  'utf8',
)

const TWO_ENVELOPE_PROFILES: BidEnvelopeProfile[] = [
  {
    mode: 'integral',
    label: '整本',
    slots: [{ id: 'integral', bidPart: null, label: '投标文件' }],
  },
  {
    mode: 'two_envelope',
    label: '第一信封 + 第二信封',
    slots: [
      { id: 'first_envelope', bidPart: 'first_envelope', label: '第一信封' },
      { id: 'second_envelope', bidPart: 'second_envelope', label: '第二信封' },
    ],
  },
]

test('商务标与技术标交错输入按卷分行且页序保持出现序', () => {
  const groups = groupSourcePageLocations([
    { documentId: 11, part: 'commercial', page: 1 },
    { documentId: 12, part: 'technical', page: 10 },
    { documentId: 11, part: 'commercial', page: 2 },
    { documentId: 12, part: 'technical', page: 3 },
  ])

  assert.deepEqual(
    groups.map((group) => ({
      partKey: group.partKey,
      label: group.label,
      pages: group.locations.map((item) => item.page),
    })),
    [
      { partKey: 'commercial', label: '商务标', pages: [1, 2] },
      { partKey: 'technical', label: '技术标', pages: [10, 3] },
    ],
  )
})

test('同形态第二例: 第一/第二信封按档案顺序分行，不写死商务技术', () => {
  const labelOf = (part: string | null | undefined): string => {
    if (part === 'first_envelope') return '第一信封'
    if (part === 'second_envelope') return '第二信封'
    return part ? part : '投标文件'
  }
  const groups = groupSourcePageLocations(
    [
      { documentId: 21, part: 'second_envelope', page: 8 },
      { documentId: 20, part: 'first_envelope', page: 4 },
      { documentId: 21, part: 'second_envelope', page: 9 },
    ],
    TWO_ENVELOPE_PROFILES,
    labelOf,
  )

  assert.deepEqual(
    groups.map((group) => ({
      partKey: group.partKey,
      label: group.label,
      pages: group.locations.map((item) => item.page),
    })),
    [
      { partKey: 'first_envelope', label: '第一信封', pages: [4] },
      { partKey: 'second_envelope', label: '第二信封', pages: [8, 9] },
    ],
  )
})

test('未分卷页码归入投标文件一行；空输入为空组', () => {
  const groups = groupSourcePageLocations([
    { documentId: 7, part: null, page: 5 },
    { documentId: 7, part: null, page: 6 },
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0]?.partKey, null)
  assert.equal(groups[0]?.label, '投标文件')
  assert.deepEqual(groups[0]?.locations.map((item) => item.page), [5, 6])
  assert.deepEqual(groupSourcePageLocations([]), [])
})

test('结果详情按卷分组渲染，不再把卷名拼进每个页码徽章', () => {
  assert.match(resultDetailSource, /groupSourcePageLocations\(pageLocations\)/)
  assert.match(resultDetailSource, /\{group\.label\}:/)
  assert.doesNotMatch(
    resultDetailSource,
    /\$\{bidPartLabel\(location\.part\)\} P\$\{location\.page\}/,
  )
})
