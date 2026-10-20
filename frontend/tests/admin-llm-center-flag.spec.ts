/**
 * 职责: 锁定 LLM Center 与财务管理一样由编译期开关控制入口
 * 不做: 不启动浏览器，不测接口
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('侧栏和路由都按 LLM Center 开关收口，运维热点不再无条件跳过去', () => {
  const features = readSource('../src/config/features.ts')
  const sidenav = readSource('../src/components/admin/AdminSideNav.tsx')
  const app = readSource('../src/App.tsx')
  const hotspot = readSource('../src/pages/admin/ops/components/HotspotPanel.tsx')

  assert.match(features, /export const isLlmCenterEnabled = parseFeatureFlag\(env\.VITE_LLM_CENTER_ENABLED\)/)
  assert.match(sidenav, /isLlmCenterEnabled \|\| item\.to !== '\/admin\/llm-center'/)
  assert.match(app, /isLlmCenterEnabled && <Route path="admin\/llm-center"/)
  assert.match(hotspot, /isLlmCenterEnabled && \(/)
})
