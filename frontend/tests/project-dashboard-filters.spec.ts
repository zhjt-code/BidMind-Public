import assert from 'node:assert/strict'
import test from 'node:test'

import type { Project } from '../src/stores/projectStore.ts'
import {
  applyProjectDashboardFilter,
  buildDashboardProjectFilter,
  parseProjectDashboardFilters,
} from '../src/pages/projectList/dashboard/projectDashboardFilters.ts'
import { filterProjectList } from '../src/pages/projectList/projectListDerivations.ts'

const projects: Project[] = [
  {
    id: 1,
    name: '写标进行中',
    projectType: 'bid_writing',
    status: 'writing',
    createdAt: '2026-07-17T02:00:00.000Z',
    updatedAt: '2026-07-17T08:00:00.000Z',
  },
  {
    id: 2,
    name: '审查完成',
    projectType: 'bid_review',
    status: 'completed',
    createdAt: '2026-07-16T23:30:00.000Z',
    updatedAt: '2026-07-17T07:00:00.000Z',
  },
  {
    id: 3,
    name: '串标待上传',
    projectType: 'collusion_check',
    status: 'created',
    createdAt: '2026-07-15T08:00:00.000Z',
    updatedAt: '2026-07-15T09:00:00.000Z',
  },
]

test('解析控制台 URL 筛选时忽略非法 type/status/stage/created', () => {
  const parsed = parseProjectDashboardFilters(new URLSearchParams(
    'type=bad&status=unknown&stage=not_a_stage&created=2026-99-99',
  ))

  assert.equal(parsed.activeTab, 'all')
  assert.equal(parsed.statusFilter, 'all')
  assert.deepEqual(parsed.exactStatuses, [])
  assert.equal(parsed.staleHours, undefined)
  assert.equal(parsed.createdDate, undefined)
  assert.equal(parsed.hasDashboardFilter, false)
})

test('控制台筛选参数支持组合、同项取消和清除', () => {
  const base = new URLSearchParams('')
  assert.equal(applyProjectDashboardFilter(base, { type: 'bid_writing' }).toString(), 'type=bid_writing')

  const combined = applyProjectDashboardFilter(
    new URLSearchParams('type=bid_writing'),
    { status: 'processing' },
  )
  assert.equal(combined.toString(), 'type=bid_writing&status=processing')

  const toggled = applyProjectDashboardFilter(
    new URLSearchParams('type=bid_writing&status=processing'),
    { status: 'processing' },
  )
  assert.equal(toggled.toString(), 'type=bid_writing')

  const stage = applyProjectDashboardFilter(
    new URLSearchParams('type=bid_writing&status=processing'),
    { stage: 'writing' },
  )
  assert.equal(stage.toString(), 'type=bid_writing&stage=writing')

  assert.equal(applyProjectDashboardFilter(stage, { clear: true }).toString(), '')
})

test('筛选列表支持业务类型、状态组、精确阶段和 UTC 创建日期', () => {
  const params = new URLSearchParams('type=bid_writing&stage=writing&created=2026-07-17')
  const dashboardFilter = buildDashboardProjectFilter(params)
  const filtered = filterProjectList({
    projects,
    activeTab: dashboardFilter.activeTab,
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: dashboardFilter.statusFilter,
    sortKey: 'updatedAt',
    dashboardFilter,
  })

  assert.deepEqual(filtered.map((project) => project.id), [1])

  const completed = buildDashboardProjectFilter(new URLSearchParams('status=completed'))
  const completedProjects = filterProjectList({
    projects,
    activeTab: completed.activeTab,
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: completed.statusFilter,
    sortKey: 'updatedAt',
    dashboardFilter: completed,
  })
  assert.deepEqual(completedProjects.map((project) => project.id), [2])
})

test('多阶段与卡滞筛选覆盖确认闸和超时处理中，不误伤已完成', () => {
  const extra = [
    ...projects,
    {
      id: 4,
      name: '目录待确认',
      projectType: 'bid_writing' as const,
      status: 'outlined',
      createdAt: '2026-07-17T01:00:00.000Z',
      updatedAt: '2026-07-17T04:00:00.000Z',
    },
    {
      id: 5,
      name: '绑定待确认',
      projectType: 'bid_review' as const,
      status: 'matched',
      createdAt: '2026-07-17T01:00:00.000Z',
      updatedAt: '2026-07-17T07:30:00.000Z',
    },
  ]
  const params = applyProjectDashboardFilter(new URLSearchParams(''), {
    stages: ['extracted', 'outlined', 'matched'],
  })
  assert.equal(params.get('stage'), 'extracted,outlined,matched')
  const confirmFilter = buildDashboardProjectFilter(params)
  const confirmed = filterProjectList({
    projects: extra,
    activeTab: confirmFilter.activeTab,
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: confirmFilter.statusFilter,
    sortKey: 'updatedAt',
    dashboardFilter: confirmFilter,
    now: new Date('2026-07-17T08:00:00.000Z'),
  })
  assert.deepEqual(confirmed.map((project) => project.id), [5, 4])

  const stalledParams = applyProjectDashboardFilter(new URLSearchParams(''), {
    status: 'processing',
    staleHours: 2,
  })
  assert.equal(stalledParams.get('status'), 'processing')
  assert.equal(stalledParams.get('stale_hours'), '2')
  const stalledFilter = buildDashboardProjectFilter(stalledParams)
  const stalled = filterProjectList({
    projects: extra,
    activeTab: stalledFilter.activeTab,
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: stalledFilter.statusFilter,
    sortKey: 'updatedAt',
    dashboardFilter: stalledFilter,
    now: new Date('2026-07-17T08:00:00.000Z'),
  })
  assert.deepEqual(stalled.map((project) => project.id), [4])
})
