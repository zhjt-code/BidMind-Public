import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  'frontend/src/pages/writing/components/WritingGateReviewSummary.tsx',
  'utf8',
)

test('review summary popover closes on outside click', () => {
  assert.match(source, /useRef/)
  assert.match(source, /document\.addEventListener\('mousedown',\s*handlePointer,\s*true\)/)
  assert.match(source, /document\.removeEventListener\('mousedown',\s*handlePointer,\s*true\)/)
  assert.match(source, /contains\(event\.target as Node\)/)
  assert.match(source, /setOpen\(false\)/)
})
