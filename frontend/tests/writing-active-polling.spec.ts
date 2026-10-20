import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const pollingPath = 'frontend/src/pages/writing/progressPolling.ts'

function readSource(path: string): string {
  return readFileSync(path, 'utf8')
}

test('writing active progress polling uses a shared 1s interval while preserving wall-clock budgets', async () => {
  assert.ok(
    existsSync(pollingPath),
    'progressPolling.ts should centralize writing active progress polling constants',
  )

  const moduleUrl = pathToFileURL(resolve(pollingPath)).href
  const constants = await import(moduleUrl)

  assert.equal(constants.WRITING_PROGRESS_POLL_INTERVAL_MS, 1_000)
  assert.equal(constants.WRITING_LONG_POLL_TIMEOUT_MS, 120 * 60_000)
  assert.equal(constants.WRITING_SINGLE_POLL_TIMEOUT_MS, 6 * 60_000)
  assert.equal(constants.WRITING_LONG_POLL_MAX_ATTEMPTS, 7_200)
  assert.equal(constants.WRITING_SINGLE_POLL_MAX_ATTEMPTS, 360)
})

test('writing progress pages consume the shared active polling constants', () => {
  const files = [
    'frontend/src/pages/writing/WritingOutline.tsx',
    'frontend/src/pages/writing/WritingUpload.tsx',
    'frontend/src/pages/writing/WritingCompose.tsx',
    'frontend/src/pages/writing/WritingBinding/constants.ts',
    'frontend/src/pages/writing/WritingMaterials/hooks/useWritingMaterialsPolling.ts',
  ]

  for (const file of files) {
    const source = readSource(file)
    assert.match(
      source,
      /WRITING_PROGRESS_POLL_INTERVAL_MS|WRITING_LONG_POLL_MAX_ATTEMPTS|WRITING_SINGLE_POLL_MAX_ATTEMPTS/,
      `${file} should use the shared writing progress polling constants`,
    )
  }
})

test('materials polling prevents overlapping status requests after shortening the interval', () => {
  const source = readSource('frontend/src/pages/writing/WritingMaterials/hooks/useWritingMaterialsPolling.ts')

  assert.match(source, /inFlightRef/)
  assert.match(source, /if\s*\(\s*inFlightRef\.current\s*\)\s*return/)
  assert.match(source, /finally\s*\{[\s\S]*inFlightRef\.current\s*=\s*false/)
})
