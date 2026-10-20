import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  forbiddenPublicClaimPatterns,
  publicClaimWhitelist,
} from '../src/content/publicClaims.ts'
import { publicHomeContent } from '../src/content/publicHome.ts'

const serializedHome = JSON.stringify(publicHomeContent)
const authLayoutSource = readFileSync(new URL('../src/components/AuthLayout.tsx', import.meta.url), 'utf8')

test('public home content presents three independent business journeys', () => {
  assert.equal(publicHomeContent.capabilities.length, 3)
  assert.deepEqual(
    publicHomeContent.capabilities.map(item => item.key),
    ['writing', 'review', 'collusion'],
  )
  assert.match(serializedHome, /独立项目/)
  assert.match(serializedHome, /标书编写/)
  assert.match(serializedHome, /投标审查/)
  assert.match(serializedHome, /串标/)
})

test('public claim whitelist only contains evidence-backed claims', () => {
  assert.ok(publicClaimWhitelist.includes('JWT 登录认证'))
  assert.ok(publicClaimWhitelist.includes('项目按创建者归属做访问控制'))
  assert.ok(publicClaimWhitelist.includes('AI 可配置为本地或云端兼容服务'))
  assert.ok(publicClaimWhitelist.includes('支持配置 HTTPS'))
  assert.ok(publicClaimWhitelist.includes('记录部分关键管理操作'))
})

test('public home content avoids unsupported enterprise promises', () => {
  for (const pattern of forbiddenPublicClaimPatterns) {
    assert.doesNotMatch(serializedHome, pattern)
  }
})

test('public home lists three deployment paths without product-wide on-prem promise', () => {
  assert.equal(publicHomeContent.deploymentPaths.length, 3)
  assert.deepEqual(
    publicHomeContent.deploymentPaths.map(item => item.key),
    ['local', 'cloud', 'hosted'],
  )
  assert.ok(publicHomeContent.deploymentPaths.every(item => !('cta' in item)))
  assert.ok(publicHomeContent.trust.includes('自建与直接注册共用同一套系统'))
  assert.ok(!('dataBoundary' in publicHomeContent))
})

test('auth layout avoids unsupported enterprise trust promises', () => {
  for (const pattern of forbiddenPublicClaimPatterns) {
    assert.doesNotMatch(authLayoutSource, pattern)
  }
})
