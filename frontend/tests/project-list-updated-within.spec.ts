import assert from 'node:assert/strict'
import test from 'node:test'

import type { Project } from '../src/stores/projectStore.ts'
import { filterProjectList } from '../src/pages/projectList/projectListDerivations.ts'

const NOW = new Date('2026-09-30T12:00:00.000+08:00')

const projects: Project[] = [
  {
    id: 1,
    name: '今日失败',
    projectType: 'bid_review',
    status: 'failed',
    createdAt: '2026-09-01T08:00:00.000+08:00',
    updatedAt: '2026-09-30T09:00:00.000+08:00',
    ownerId: 1,
    owner: { id: 1, username: 'alice' },
  },
  {
    id: 2,
    name: '昨日待上传',
    projectType: 'bid_writing',
    status: 'created',
    createdAt: '2026-09-20T08:00:00.000+08:00',
    updatedAt: '2026-09-29T18:00:00.000+08:00',
    ownerId: 1,
    owner: { id: 1, username: 'alice' },
  },
  {
    id: 3,
    name: '十天前完成',
    projectType: 'bid_review',
    status: 'completed',
    createdAt: '2026-09-01T08:00:00.000+08:00',
    updatedAt: '2026-09-20T08:00:00.000+08:00',
    ownerId: 2,
    owner: { id: 2, username: 'bob' },
  },
  {
    id: 4,
    name: '四十天前失败',
    projectType: 'collusion_check',
    status: 'failed',
    createdAt: '2026-08-01T08:00:00.000+08:00',
    updatedAt: '2026-08-20T08:00:00.000+08:00',
    ownerId: 2,
    owner: { id: 2, username: 'bob' },
  },
]

test('updatedWithin 按本地日界过滤今天 / 近7天 / 近30天 / 全部', () => {
  const today = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'all',
    sortKey: 'updatedAt',
    updatedWithin: 'today',
    now: NOW,
  })
  assert.deepEqual(today.map((p) => p.id), [1])

  const week = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'all',
    sortKey: 'updatedAt',
    updatedWithin: '7d',
    now: NOW,
  })
  assert.deepEqual(week.map((p) => p.id), [1, 2])

  const month = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'all',
    sortKey: 'updatedAt',
    updatedWithin: '30d',
    now: NOW,
  })
  assert.deepEqual(month.map((p) => p.id), [1, 2, 3])

  const all = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'all',
    sortKey: 'updatedAt',
    updatedWithin: 'all',
    now: NOW,
  })
  assert.deepEqual(all.map((p) => p.id), [1, 2, 3, 4])
})

test('updatedWithin 与 statusFilter 可组合（同形态第二例：失败 ∩ 今天）', () => {
  const failedToday = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'failed',
    sortKey: 'updatedAt',
    updatedWithin: 'today',
    now: NOW,
  })
  assert.deepEqual(failedToday.map((p) => p.id), [1])

  const pendingWeek = filterProjectList({
    projects,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'pending',
    sortKey: 'updatedAt',
    updatedWithin: '7d',
    now: NOW,
  })
  assert.deepEqual(pendingWeek.map((p) => p.id), [2])
})
