import assert from 'node:assert/strict'
import test from 'node:test'

import type { Project } from '../src/stores/projectStore.ts'
import {
  buildProjectListStats,
  filterProjectList,
  getCreateProjectPreset,
  getProjectListContext,
  getScopedProjects,
  resolveProjectListTab,
} from '../src/pages/projectList/projectListDerivations.ts'

const NOW = new Date('2026-07-12T08:00:00.000Z')

const projects: Project[] = [
  {
    id: 1,
    name: '写标方案',
    projectType: 'bid_writing',
    status: 'writing',
    createdAt: '2026-07-01T08:00:00.000Z',
    updatedAt: '2026-07-12T08:00:00.000Z',
    ownerId: 10,
    owner: { id: 10, username: 'alice' },
  },
  {
    id: 2,
    name: '投标审查 A',
    projectType: 'bid_review',
    status: 'completed',
    createdAt: '2026-07-02T08:00:00.000Z',
    updatedAt: '2026-07-11T08:00:00.000Z',
    ownerId: 11,
    owner: { id: 11, username: 'bob' },
  },
  {
    id: 3,
    name: '投标审查 B',
    projectType: 'bid_review',
    status: 'failed',
    createdAt: '2026-06-30T08:00:00.000Z',
    updatedAt: '2026-07-10T08:00:00.000Z',
    ownerId: 10,
    owner: { id: 10, username: 'alice' },
  },
  {
    id: 4,
    name: '串标检查',
    projectType: 'collusion_check',
    status: 'reviewing',
    createdAt: '2026-07-03T08:00:00.000Z',
    updatedAt: '2026-07-09T08:00:00.000Z',
    ownerId: 12,
    owner: { id: 12, username: 'carol' },
  },
]

test('URL type 解析仅接受三类业务域, 其它值回退全部', () => {
  assert.equal(resolveProjectListTab('bid_writing'), 'bid_writing')
  assert.equal(resolveProjectListTab('bid_review'), 'bid_review')
  assert.equal(resolveProjectListTab('collusion_check'), 'collusion_check')
  assert.equal(resolveProjectListTab('unknown'), 'all')
  assert.equal(resolveProjectListTab(null), 'all')
})

test('业务域上下文标题、总数和创建预选与当前 type 对齐', () => {
  assert.deepEqual(getProjectListContext(projects, 'all'), {
    title: '项目控制台',
    description: '查看项目运行态势，按业务和状态快速定位项目',
    total: 4,
    iconName: 'House',
  })
  assert.deepEqual(getProjectListContext(projects, 'bid_review'), {
    title: '投标审查项目',
    description: '查看和管理投标审查项目',
    total: 2,
    iconName: 'ClipboardCheck',
  })
  assert.equal(getProjectListContext(projects, 'bid_writing').iconName, 'PenTool')
  assert.equal(getProjectListContext(projects, 'collusion_check').iconName, 'ShieldAlert')
  assert.equal(getCreateProjectPreset('all'), undefined)
  assert.equal(getCreateProjectPreset('bid_writing'), 'bid_writing')
  assert.equal(getCreateProjectPreset('bid_review'), 'bid_review')
  assert.equal(getCreateProjectPreset('collusion_check'), 'collusion_check')
})

test('统计在全部页展示全局分布, 在业务页只统计当前业务域', () => {
  assert.deepEqual(buildProjectListStats(projects, 'all', NOW), {
    total: 4,
    pending: 0,
    processing: 2,
    completed: 1,
    failed: 1,
    bidWriting: 1,
    bidReview: 2,
    collusionCheck: 1,
    thisMonth: 3,
    showTypeDistribution: true,
  })

  assert.deepEqual(buildProjectListStats(projects, 'bid_review', NOW), {
    total: 2,
    pending: 0,
    processing: 0,
    completed: 1,
    failed: 1,
    bidWriting: 0,
    bidReview: 2,
    collusionCheck: 0,
    thisMonth: 1,
    showTypeDistribution: false,
  })
})

test('状态桶与列表筛选同一分区，待上传与进行中互不吞并', () => {
  const partitioned: Project[] = [
    {
      id: 11,
      name: '写标待上传',
      projectType: 'bid_writing',
      status: 'created',
      createdAt: '2026-07-04T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 10,
    },
    {
      id: 12,
      name: '串标待上传',
      projectType: 'collusion_check',
      status: 'created',
      createdAt: '2026-07-05T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 12,
    },
    {
      id: 13,
      name: '审查待继续',
      projectType: 'bid_review',
      status: 'parsed',
      createdAt: '2026-07-06T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 11,
    },
    {
      id: 14,
      name: '写标生成中',
      projectType: 'bid_writing',
      status: 'writing',
      createdAt: '2026-07-07T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 10,
    },
    {
      id: 15,
      name: '审查已完成',
      projectType: 'bid_review',
      status: 'completed',
      createdAt: '2026-07-08T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 11,
    },
    {
      id: 16,
      name: '串标失败',
      projectType: 'collusion_check',
      status: 'failed',
      createdAt: '2026-07-09T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 12,
    },
  ]

  const stats = buildProjectListStats(partitioned, 'all', NOW)
  assert.equal(stats.pending, 2)
  assert.equal(stats.processing, 2)
  assert.equal(stats.completed, 1)
  assert.equal(stats.failed, 1)
  assert.equal(stats.pending + stats.processing + stats.completed + stats.failed, stats.total)

  const writing = buildProjectListStats(partitioned, 'bid_writing', NOW)
  assert.equal(writing.pending, 1)
  assert.equal(writing.processing, 1)
  assert.equal(writing.pending + writing.processing + writing.completed + writing.failed, writing.total)

  const pendingOnly = filterProjectList({
    projects: partitioned,
    activeTab: 'all',
    searchQuery: '',
    usernameQuery: '',
    isAdmin: false,
    statusFilter: 'pending',
    sortKey: 'updatedAt',
  })
  assert.deepEqual(pendingOnly.map((project) => project.id).sort((a, b) => a - b), [11, 12])
})

test('项目筛选先限定业务域, 管理员用户名筛选仍在域内生效', () => {
  assert.deepEqual(getScopedProjects(projects, 'collusion_check').map((p) => p.id), [4])

  const filtered = filterProjectList({
    projects,
    activeTab: 'bid_review',
    searchQuery: '',
    usernameQuery: 'ali',
    isAdmin: true,
    statusFilter: 'failed',
    sortKey: 'updatedAt',
  })

  assert.deepEqual(filtered.map((p) => p.id), [3])
})
