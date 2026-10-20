import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('WritingCompose exposes draft source clause ids and renders clause coverage panel', () => {
  const api = readFileSync('src/services/writingApi.ts', 'utf8')
  const compose = readFileSync('src/pages/writing/WritingCompose.tsx', 'utf8')

  assert.match(api, /sourceClauseIds:\s*number\[\]/)
  assert.match(api, /source_clause_ids/)
  assert.match(compose, /WritingClauseCoveragePanel/)
  assert.match(compose, /loadClauses/)
})

test('WritingClauseCoveragePanel defaults collapsed and resets when node changes', () => {
  const panel = readFileSync('src/pages/writing/WritingClauseCoveragePanel.tsx', 'utf8')

  assert.match(panel, /useState\(true\)/)
  assert.match(panel, /useEffect\(\(\)\s*=>\s*\{\s*setCollapsed\(true\)/)
  assert.match(panel, /\[currentNodeId\]/)
  assert.match(panel, /aria-expanded=\{!collapsed\}/)
  assert.match(panel, /本章约束来源\s*·/)
  assert.match(panel, /!\s*collapsed\s*&&/)
})
