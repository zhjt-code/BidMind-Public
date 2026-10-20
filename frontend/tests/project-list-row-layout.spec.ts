import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  formatListAge,
  hasDistinctListUpdate,
  projectListColumnLabels,
  projectListGridTracks,
} from '../src/pages/projectList/projectListRowLayout.ts'
import { listProgressPercent } from '../src/pages/projectList/types.ts'

const NOW = new Date('2026-09-29T02:00:00.000Z').getTime()
const DAY_MS = 24 * 60 * 60 * 1000

test('管理员与普通用户的列轨数量与表头一致，创建者列只出现在管理员', () => {
  const cases = [
    { isAdmin: true, selectionMode: false },
    { isAdmin: false, selectionMode: false },
    { isAdmin: true, selectionMode: true },
    { isAdmin: false, selectionMode: true },
  ]
  for (const entry of cases) {
    const labels = projectListColumnLabels(entry.isAdmin, entry.selectionMode)
    const tracks = projectListGridTracks(entry.isAdmin, entry.selectionMode)
    assert.equal(tracks.length, labels.length)
    const owner = labels.find((column) => column.key === 'owner')
    const nameAt = labels.findIndex((column) => column.key === 'name')
    if (entry.isAdmin) {
      assert.equal(owner?.label, '创建者')
      assert.equal(tracks.includes('8.5rem'), true)
      assert.equal(tracks[nameAt], 'minmax(0, 1fr)')
      assert.equal(tracks.at(-1), '12rem')
      const statusTrack = tracks.indexOf('6.75rem')
      assert.equal(tracks[statusTrack - 1], '6rem')
    } else {
      assert.equal(owner, undefined)
      assert.equal(tracks.includes('8.5rem'), false)
      assert.equal(tracks[nameAt], 'minmax(0, 2.5fr)')
      assert.equal(tracks[nameAt + 1], 'minmax(6.5rem, 1fr)')
      assert.equal(tracks[nameAt + 2], 'minmax(6.5rem, 1fr)')
      assert.equal(tracks.at(-1), 'minmax(12rem, 1fr)')
      const statusTrack = tracks.indexOf('minmax(6.75rem, 1fr)')
      assert.equal(tracks[statusTrack - 1], 'minmax(6rem, 1fr)')
    }
  }
})

test('选择列与创建者列是可缺的轨道，普通用户批量选择不留创建者空列', () => {
  assert.equal(projectListGridTracks(true, true)[0], '1.25rem')
  assert.equal(projectListGridTracks(false, true)[0], '1.25rem')
  assert.equal(projectListColumnLabels(false, true).some((column) => column.key === 'owner'), false)
  assert.equal(projectListColumnLabels(true, false)[0]?.key, 'name')
  assert.deepEqual(
    projectListColumnLabels(true, false).map((column) => column.label),
    ['项目名称', '创建者', '创建时间', '更新时间', '进度', '状态', '操作'],
  )
  assert.equal(projectListColumnLabels(false, false).some((column) => column.label === '当前进度'), false)
  assert.equal(projectListColumnLabels(true, true).some((column) => column.label === '标书编写'), false)
  for (const isAdmin of [true, false]) {
    const labels = projectListColumnLabels(isAdmin, false)
    assert.equal(labels.find((column) => column.key === 'progress')?.align, 'center')
    const progressAt = labels.findIndex((column) => column.key === 'progress')
    assert.equal(labels[progressAt - 1]?.key, 'updated')
    assert.equal(labels[progressAt + 1]?.key, 'status')
    assert.equal(labels.find((column) => column.key === 'status')?.align, 'center')
    assert.equal(labels.find((column) => column.key === 'action')?.align, 'center')
    assert.equal(labels.find((column) => column.key === 'created')?.align, 'start')
  }
})

test('列表进度按步骤阶梯取整，失败不占用完成百分比', () => {
  assert.equal(listProgressPercent('reviewing', 'bid_review'), 83)
  assert.equal(listProgressPercent('formatting', 'bid_writing'), 92)
  assert.equal(listProgressPercent('completed', 'bid_review'), 100)
  assert.equal(listProgressPercent('failed', 'collusion_check'), null)
  assert.equal(listProgressPercent('failed', 'bid_writing'), null)
})

test('列表时间用短文案，更新时间与创建时间相同则不占差异', () => {
  assert.equal(formatListAge(new Date(NOW - 16 * DAY_MS).toISOString(), NOW), '16天前')
  assert.equal(formatListAge(new Date(NOW - 40 * DAY_MS).toISOString(), NOW), '1个月前')
  assert.equal(formatListAge(new Date(NOW - 400 * DAY_MS).toISOString(), NOW), '1年前')
  assert.equal(formatListAge('not-a-date', NOW), '未知时间')

  const same = '2026-09-01T00:00:00.000Z'
  assert.equal(hasDistinctListUpdate(same, same), false)
  assert.equal(hasDistinctListUpdate(same, null), false)
  assert.equal(hasDistinctListUpdate(same, '2026-09-13T00:00:00.000Z'), true)
  assert.equal(hasDistinctListUpdate('2026-01-02T00:00:00.000Z', '2026-08-01T00:00:00.000Z'), true)
})

test('项目行不再渲染业务类型文字徽标，表头与行共用列轨', () => {
  const cardSource = readFileSync(new URL('../src/pages/projectList/ProjectCard.tsx', import.meta.url), 'utf8')
  const contentSource = readFileSync(new URL('../src/pages/projectList/ProjectListContent.tsx', import.meta.url), 'utf8')
  assert.match(cardSource, /projectListGridTemplate/)
  assert.match(contentSource, /projectListColumnLabels/)
  assert.match(contentSource, /projectListGridTemplate/)
  assert.doesNotMatch(cardSource, /typeCfg\.label/)
  assert.doesNotMatch(cardSource, /PROJECT_TYPE_CONFIG/)
  assert.doesNotMatch(cardSource, /FileText/)
  assert.match(cardSource, /ClipboardCheck/)
  assert.match(cardSource, /border-primary\/15 bg-primary\/\[0\.08\]/)
  assert.match(cardSource, /h-\[18px\] w-\[18px\]/)
  assert.match(cardSource, /w-full justify-center/)
  assert.match(contentSource, /projectListColumnAlignClass/)
  assert.match(contentSource, /whitespace-nowrap/)
  assert.match(cardSource, /bg-red-100/)
  assert.match(cardSource, /'fail'/)
  assert.doesNotMatch(cardSource, /aria-label="进度 失败">失败/)
  const layoutCss = readFileSync(new URL('../src/pages/projectList/projectListRowLayout.css', import.meta.url), 'utf8')
  assert.match(layoutCss, /min-width:\s*64rem/)
})
