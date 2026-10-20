import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const hookPath = 'frontend/src/hooks/useProgressAnimation.ts'
const projectionPath = 'frontend/src/hooks/progressProjection.ts'
const genericPath = 'frontend/src/components/GenericProgressPage.tsx'
const panelPath = 'frontend/src/components/ProgressPanel.tsx'
const inlinePath = 'frontend/src/components/InlineProgressBar.tsx'

function readSource(path: string): string {
  return readFileSync(path, 'utf8')
}

test('progress projection module projects backend targets without animation or monotonic clamp', async () => {
  assert.ok(
    existsSync(projectionPath),
    'progressProjection.ts should define the pure direct-projection contract',
  )

  const moduleUrl = pathToFileURL(resolve(projectionPath)).href
  const { projectProgressSnapshot } = await import(moduleUrl)
  const steps = [
    {
      id: 'extract',
      name: '投标响应提取',
      status: 'processing',
      progress: 0.8,
      targetProgress: 0.3,
      detail: '3/10',
    },
    {
      id: 'review',
      name: 'AI对比审查',
      status: 'pending',
      progress: 0.2,
      targetProgress: 0,
    },
  ]

  const snapshot = projectProgressSnapshot(steps, 0.3)

  assert.equal(snapshot.displayProgress, 0.3)
  assert.equal(snapshot.steps[0].progress, 0.3)
  assert.equal(snapshot.steps[0].targetProgress, 0.3)
  assert.equal(snapshot.steps[0].status, 'processing')
  assert.equal(snapshot.steps[1].progress, 0)
  assert.equal(snapshot.steps[1].status, 'pending')
})

test('useProgressAnimation no longer schedules RAF or keeps stale monotonic targets', () => {
  const source = readSource(hookPath)

  assert.doesNotMatch(source, /requestAnimationFrame|cancelAnimationFrame/)
  assert.doesNotMatch(source, /EASING_FACTOR|CONVERGE_THRESHOLD/)
  assert.doesNotMatch(source, /prevStep\?\.targetProgress/)
  assert.match(source, /projectProgressSnapshot/)
})

test('GenericProgressPage must not promote a pending step to processing', () => {
  const source = readSource(genericPath)

  assert.doesNotMatch(source, /firstPending/)
  assert.doesNotMatch(source, /steps\.some\(\(s\) => s\.status === 'processing'\)/)
})

test('ProgressPanel and InlineProgressBar do not manufacture a 3 percent floor or delayed status color', () => {
  const panel = readSource(panelPath)
  const inline = readSource(inlinePath)

  assert.doesNotMatch(panel, /displayProgress < 0\.03/)
  assert.doesNotMatch(panel, /duration-500/)
  assert.doesNotMatch(inline, /displayProgress < 0\.03/)
  assert.doesNotMatch(inline, /safeProgress > 0/)
})

test('processing progress fills use the shared barber-stripe class', () => {
  const panel = readSource(panelPath)
  const inline = readSource(inlinePath)
  const css = readSource('frontend/src/index.css')

  assert.match(panel, /progress-stripe/)
  assert.match(inline, /progress-stripe/)
  assert.match(css, /@keyframes progress-stripe-slide/)
  assert.match(css, /prefers-reduced-motion: reduce\) \{\s*\.progress-stripe \{\s*animation: none/)
})
