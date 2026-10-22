import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  APP_HOME,
  LOGIN_PATH,
  PUBLIC_HOME,
  REGISTER_PATH,
  buildAppProjectListPath,
} from '../src/config/routes.ts'

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const protectedRouteSource = readFileSync(new URL('../src/components/ProtectedRoute.tsx', import.meta.url), 'utf8')
const headerSource = readFileSync(new URL('../src/components/Header.tsx', import.meta.url), 'utf8')
const loginSource = readFileSync(new URL('../src/pages/Login.tsx', import.meta.url), 'utf8')
const registerSource = readFileSync(new URL('../src/pages/Register.tsx', import.meta.url), 'utf8')
const workspaceReturnSources = [
  readFileSync(new URL('../src/pages/FileUpload.tsx', import.meta.url), 'utf8'),
  readFileSync(new URL('../src/pages/CollusionUpload.tsx', import.meta.url), 'utf8'),
  readFileSync(new URL('../src/pages/CollusionResult.tsx', import.meta.url), 'utf8'),
  readFileSync(new URL('../src/pages/Settings.tsx', import.meta.url), 'utf8'),
  readFileSync(new URL('../src/pages/writing/WritingUpload.tsx', import.meta.url), 'utf8'),
  readFileSync(new URL('../src/pages/writing/useWritingGuard.ts', import.meta.url), 'utf8'),
]

test('public landing and authenticated workspace use separate root paths', () => {
  assert.equal(PUBLIC_HOME, '/')
  assert.equal(APP_HOME, '/app')
  assert.equal(LOGIN_PATH, '/login')
  assert.equal(REGISTER_PATH, '/register')
})

test('project list filters are built under the authenticated workspace', () => {
  assert.equal(buildAppProjectListPath(), '/app')
  assert.equal(buildAppProjectListPath('bid_review'), '/app?type=bid_review')
  assert.equal(buildAppProjectListPath('bid_writing'), '/app?type=bid_writing')
  assert.equal(buildAppProjectListPath('collusion_check'), '/app?type=collusion_check')
})

test('application shell mounts authenticated workspace under app path', () => {
  assert.match(appSource, /path="app" element=\{<ProjectListPage \/>/)
  assert.match(appSource, /if \(isAuthenticated\) return <Navigate to=\{APP_HOME\} replace \/>/)
  assert.match(protectedRouteSource, /<Navigate to=\{APP_HOME\} replace \/>/)
})

test('workspace navigation does not point authenticated users back to public root', () => {
  assert.match(headerSource, /to: APP_HOME/)
  assert.match(headerSource, /buildAppProjectListPath\('bid_writing'\)/)
  assert.match(headerSource, /<Link to=\{APP_HOME\}/)
  assert.match(loginSource, /\?\? APP_HOME|\|\| APP_HOME/)
  assert.match(registerSource, /navigate\(APP_HOME, \{ replace: true \}\)/)

  for (const source of workspaceReturnSources) {
    assert.doesNotMatch(source, /navigate\(['"]\/['"]\)/)
  }
})
