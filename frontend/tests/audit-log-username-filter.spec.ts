import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const auditLogSource = readFileSync(
  new URL('../src/pages/AuditLog.tsx', import.meta.url),
  'utf8',
)

test('审计日志筛选栏提供独立的用户名搜索输入', () => {
  assert.match(auditLogSource, /placeholder="搜索用户名\.\.\."/)
  assert.match(auditLogSource, /aria-label="按用户名搜索"/)
  assert.match(auditLogSource, /const \[username, setUsername\] = useState\(''\)/)
  assert.match(auditLogSource, /const \[usernameInput, setUsernameInput\] = useState\(''\)/)
})

test('用户名搜索写入 activeFilters 并与详情关键词并存', () => {
  assert.match(auditLogSource, /if \(username\) filters\.username = username/)
  assert.match(auditLogSource, /if \(keyword\) filters\.keyword = keyword/)
  assert.match(auditLogSource, /setUsername\(usernameInput\.trim\(\)\)/)
  assert.match(auditLogSource, /setKeyword\(searchInput\.trim\(\)\)/)
})

test('重置筛选同时清空用户名输入与已生效条件', () => {
  assert.match(auditLogSource, /setUsername\(''\)/)
  assert.match(auditLogSource, /setUsernameInput\(''\)/)
  assert.match(
    auditLogSource,
    /categoryFilter !== 'all' \|\| actionFilter !== 'all' \|\| resourceFilter !== 'all' \|\| username \|\| keyword/,
  )
})
