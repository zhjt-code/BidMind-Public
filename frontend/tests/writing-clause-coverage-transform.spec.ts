import assert from 'node:assert/strict'
import test from 'node:test'

import { transformClauseCoverage } from '../src/services/writingClauseCoverageTransforms.ts'

test('transform: 后端 snake_case 字段映射为 camelCase', () => {
  const result = transformClauseCoverage({
    counted: 149,
    covered: 90,
    uncovered: 59,
    pending_assignment: { count: 12, samples: [{ id: 1, summary: '资质要求' }] },
    unmatched: { count: 18, samples: [] },
    pending_generation: { count: 29, samples: [] },
  })

  assert.equal(result.counted, 149)
  assert.equal(result.covered, 90)
  assert.equal(result.uncovered, 59)
  assert.equal(result.pendingAssignment.count, 12)
  assert.equal(result.pendingAssignment.samples[0].id, 1)
  assert.equal(result.pendingAssignment.samples[0].summary, '资质要求')
  assert.equal(result.unmatched.count, 18)
  assert.equal(result.pendingGeneration.count, 29)
})

test('transform: 缺失/异常字段安全降级为 0 与空数组', () => {
  const result = transformClauseCoverage({})

  assert.equal(result.counted, 0)
  assert.equal(result.covered, 0)
  assert.equal(result.uncovered, 0)
  assert.equal(result.pendingAssignment.count, 0)
  assert.deepEqual(result.pendingAssignment.samples, [])
  assert.equal(result.unmatched.count, 0)
  assert.equal(result.pendingGeneration.count, 0)
})

test('transform: samples 非数组时不抛错', () => {
  const result = transformClauseCoverage({
    counted: 1,
    covered: 0,
    uncovered: 1,
    unmatched: { count: 1, samples: null },
  })

  assert.deepEqual(result.unmatched.samples, [])
  assert.equal(result.unmatched.count, 1)
})

test('transform: by_sub_type 转换为 bySubType 消费率明细', () => {
  const result = transformClauseCoverage({
    by_sub_type: {
      content_required: {
        counted: 2,
        covered: 1,
        uncovered: 1,
        reference: 0,
        check_only: 0,
      },
      disqualification: {
        counted: 0,
        covered: 0,
        uncovered: 0,
        reference: 0,
        check_only: 3,
      },
    },
  })

  assert.equal(result.bySubType.content_required.counted, 2)
  assert.equal(result.bySubType.content_required.covered, 1)
  assert.equal(result.bySubType.disqualification.checkOnly, 3)
})
