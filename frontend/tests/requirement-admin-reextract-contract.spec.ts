/**
 * 防御测试：管理员重新提取招标要求入口。
 *
 * 锁定不变量：
 *   1. RequirementHeader 中“重新提取”仅由 isAdmin 控制显示，且弱于主按钮。
 *   2. RequirementReview 主页面必须提供二次确认弹窗，避免误清空旧确认清单。
 *   3. 确认后必须调用 requirementService.reextractRequirements，并复用提取轮询展示进度。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const read = (rel: string): string =>
  readFileSync(join(import.meta.dirname, rel), 'utf-8')

test('RequirementHeader 仅对管理员展示弱化的重新提取入口', () => {
  const source = read('../src/pages/RequirementReview/components/RequirementHeader.tsx')

  assert.ok(source.includes('isAdmin'), '页头必须接收 isAdmin 控制入口可见性')
  assert.ok(source.includes('重新提取'), '必须渲染“重新提取”入口')
  assert.ok(source.includes('variant="outline"'), '重新提取必须是弱化 outline 按钮')
  assert.ok(source.includes('{isAdmin &&'), '重新提取入口必须仅管理员可见')
})

test('RequirementReview 主页面接入二次确认弹窗与重提取服务', () => {
  const source = read('../src/pages/RequirementReview/index.tsx')

  assert.ok(source.includes('ReextractConfirmDialog'), '必须提供二次确认弹窗')
  assert.ok(source.includes('showReextractDialog'), '必须维护重提取确认弹窗状态')
  assert.ok(
    source.includes('requirementService.reextractRequirements'),
    '确认后必须调用管理员重提取 API',
  )
  assert.ok(
    source.includes('startExtractionIfNeeded()'),
    '重提取入队后必须复用既有提取轮询展示进度',
  )
})

test('requirementService 暴露 reextractRequirements API 封装', () => {
  const source = read('../src/services/requirementApi.ts')

  assert.ok(source.includes('reextractRequirements'), '必须封装管理员重提取 API')
  assert.ok(source.includes('/requirements/reextract'), '必须调用 /requirements/reextract')
})
