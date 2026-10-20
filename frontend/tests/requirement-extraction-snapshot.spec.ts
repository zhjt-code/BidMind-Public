import assert from 'node:assert/strict'
import test from 'node:test'
import {
  advanceExtractionSnapshotIdentity,
  readExtractionSnapshotIdentity,
  shouldAcceptExtractionSnapshot,
} from '../src/pages/RequirementReview/hooks/extractionSnapshotIdentity.ts'

test('readExtractionSnapshotIdentity requires both run_id and revision', () => {
  assert.equal(readExtractionSnapshotIdentity({}), null)
  assert.equal(readExtractionSnapshotIdentity({ run_id: 'abc' }), null)
  assert.equal(readExtractionSnapshotIdentity({ revision: 1 }), null)
  assert.deepEqual(readExtractionSnapshotIdentity({ run_id: 'abc', revision: 2 }), {
    runId: 'abc',
    revision: 2,
  })
})

test('shouldAcceptExtractionSnapshot accepts first snapshot before active is established', () => {
  assert.equal(shouldAcceptExtractionSnapshot(null, null), true)
  assert.equal(
    shouldAcceptExtractionSnapshot(null, { runId: 'r1', revision: 1 }),
    true,
  )
})

test('shouldAcceptExtractionSnapshot rejects missing identity after active exists', () => {
  assert.equal(
    shouldAcceptExtractionSnapshot({ runId: 'r1', revision: 1 }, null),
    false,
  )
})

test('shouldAcceptExtractionSnapshot rejects different run even with higher revision', () => {
  assert.equal(
    shouldAcceptExtractionSnapshot(
      { runId: 'r1', revision: 1 },
      { runId: 'r2', revision: 99 },
    ),
    false,
  )
})

test('shouldAcceptExtractionSnapshot rejects lower or equal revision on same run', () => {
  assert.equal(
    shouldAcceptExtractionSnapshot(
      { runId: 'r1', revision: 5 },
      { runId: 'r1', revision: 5 },
    ),
    false,
  )
  assert.equal(
    shouldAcceptExtractionSnapshot(
      { runId: 'r1', revision: 5 },
      { runId: 'r1', revision: 4 },
    ),
    false,
  )
})

test('shouldAcceptExtractionSnapshot accepts higher revision on same run', () => {
  assert.equal(
    shouldAcceptExtractionSnapshot(
      { runId: 'r1', revision: 5 },
      { runId: 'r1', revision: 6 },
    ),
    true,
  )
})

test('advanceExtractionSnapshotIdentity tracks highest accepted revision', () => {
  const first = advanceExtractionSnapshotIdentity(null, {
    runId: 'r1',
    revision: 1,
  })
  assert.deepEqual(first, { runId: 'r1', revision: 1 })

  const next = advanceExtractionSnapshotIdentity(first, {
    runId: 'r1',
    revision: 3,
  })
  assert.deepEqual(next, { runId: 'r1', revision: 3 })

  const ignored = advanceExtractionSnapshotIdentity(next, {
    runId: 'r1',
    revision: 2,
  })
  assert.deepEqual(ignored, { runId: 'r1', revision: 3 })

  const otherRun = advanceExtractionSnapshotIdentity(next, {
    runId: 'r2',
    revision: 1,
  })
  assert.deepEqual(otherRun, { runId: 'r1', revision: 3 })
})
