import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const pageSource = readFileSync(
  new URL('../src/pages/UserManagement.tsx', import.meta.url),
  'utf8',
)
const userApiSource = readFileSync(
  new URL('../src/services/userApi.ts', import.meta.url),
  'utf8',
)

test('用户管理状态筛选提供在线用户选项', () => {
  assert.match(pageSource, /<option value="online">在线用户<\/option>/)
  assert.match(
    pageSource,
    /isOnline: statusFilter === 'online' \? true : undefined/,
  )
  assert.match(
    pageSource,
    /isActive: statusFilter === 'active' \? true : statusFilter === 'inactive' \? false : undefined/,
  )
})

test('选择在线用户时不会把 is_active 误传为 false', () => {
  assert.doesNotMatch(
    pageSource,
    /isActive: statusFilter === '' \? undefined : statusFilter === 'active'/,
  )
})

test('用户列表 API 会转发 is_online 查询参数', () => {
  assert.match(userApiSource, /isOnline\?: boolean/)
  assert.match(
    userApiSource,
    /if \(params\?\.isOnline !== undefined\) queryParams\.append\('is_online', String\(params\.isOnline\)\)/,
  )
})
